"""持久化后台任务队列（002-enterprise-cli-upgrade US4 T032，FR-009/010，R-08）。

DurableTaskManager：stdlib `sqlite3` 原子租约队列，零新依赖（R-08）。
表结构对齐 contracts/runtime.md：
  tasks(id, scope, state, payload, lease_owner, lease_until, created_at, updated_at)

关键保证：
- **原子领取**：`BEGIN IMMEDIATE` + `UPDATE ... WHERE state='queued' AND lease_until<=now`
  （先读后写同一事务，多 worker 并发不会重复领取）。
- **心跳续租**：`heartbeat` 刷新 lease_until，防存活 worker 被误判过期。
- **崩溃恢复**：`requeue_expired` 把租约过期的 running 任务重置为 queued（SC-006）。
- **取消竞态（FR-010）**：`cancel` 置 canceled；`complete/fail` 用
  `WHERE state != 'canceled'` 迁移——canceled 优先，迟到结果不覆盖。
- **per-scope 隔离**：`scope` = 项目目录；领取/查询按 scope 过滤（FR-009）。

时钟可注入（`clock` 返回 UTC datetime），测试可确定性推进租约；
时间戳以固定宽 `YYYY-MM-DDTHH:MM:SS.mmmZ`（UTC，毫秒）落库，保证字典序可比。
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from GSagent.common import DEFAULT_RUNTIME_DB

# 状态机（contracts/runtime.md）：queued → running → completed/failed/canceled
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,
    state       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    lease_owner TEXT,
    lease_until TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_claim
    ON tasks (scope, state, created_at);
"""


def _iso(ts: datetime) -> str:
    """UTC datetime → 固定宽 ISO 字符串（毫秒 + Z，字典序即时间序）。"""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def queue_db_path(config: Any) -> Path:
    """从配置解析任务队列 SQLite 路径（runtime.queue_db，空=./.GSagent/runtime.db）。

    供 serve（server.py）与 `agent tasks`（main.py）统一使用（B：单一路径来源）。
    """
    queue_db = str((config.data.get("runtime") or {}).get("queue_db") or "")
    if queue_db:
        return Path(queue_db).expanduser()
    return DEFAULT_RUNTIME_DB


class DurableTaskManager:
    """SQLite 持久化任务队列（原子租约 + 取消保护 + per-scope 隔离）。"""

    def __init__(
        self,
        path: Union[str, Path],
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.path = str(path)
        self._clock = clock or _default_clock
        # RLock：同进程内多 worker 线程的写串行化（sqlite 写锁之外再包一层，
        # 保证 BEGIN IMMEDIATE 内无交错）。
        self._lock = threading.RLock()
        self._ensure_schema()

    # ---- 连接 ----
    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None：事务手动控制（BEGIN IMMEDIATE / COMMIT / ROLLBACK）。
        con = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self) -> None:
        con = self._connect()
        try:
            # 多语句 DDL：executescript 自动提交并允许一次执行多条
            con.executescript(_SCHEMA)
        finally:
            con.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.get("payload") or "{}")
        except ValueError:
            data["payload"] = {}
        return data

    def _now(self) -> datetime:
        return self._clock()

    def _iso_now(self) -> str:
        return _iso(self._now())

    # ---- 投递 ----
    def create(self, scope: str, payload: Dict[str, Any]) -> str:
        """投递一个后台任务（queued），返回任务 id。"""
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        now = self._iso_now()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "INSERT INTO tasks "
                    "(id, scope, state, payload, lease_owner, lease_until, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                    (
                        task_id,
                        scope,
                        STATE_QUEUED,
                        json.dumps(payload or {}, ensure_ascii=False),
                        now,  # queued 任务立即可领取
                        now,
                        now,
                    ),
                )
            finally:
                con.close()
        return task_id

    # ---- 领取（原子租约）----
    def claim_next(
        self, scope: str, worker_id: str, lease_secs: int
    ) -> Optional[Dict[str, Any]]:
        """原子领取 scope 内最早的可执行任务；无则返回 None。

        领取 = 事务内「读最旧 queued → 置 running + 租约」，多 worker 并发下
        同一任务只被一个 worker 领走（BEGIN IMMEDIATE 持有写锁直到 COMMIT）。
        """
        now = self._now()
        lease_until = now + timedelta(seconds=lease_secs)
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT * FROM tasks WHERE scope=? AND state=? AND lease_until<=? "
                    "ORDER BY created_at LIMIT 1",
                    (scope, STATE_QUEUED, _iso(now)),
                ).fetchone()
                if row is None:
                    con.execute("ROLLBACK")
                    return None
                task_id = row["id"]
                con.execute(
                    "UPDATE tasks SET state=?, lease_owner=?, lease_until=?, updated_at=? "
                    "WHERE id=? AND state=?",
                    (
                        STATE_RUNNING,
                        worker_id,
                        _iso(lease_until),
                        _iso(now),
                        task_id,
                        STATE_QUEUED,
                    ),
                )
                con.execute("COMMIT")
                rec = self._row_to_dict(row)
                rec.update(
                    {
                        "state": STATE_RUNNING,
                        "lease_owner": worker_id,
                        "lease_until": _iso(lease_until),
                        "updated_at": _iso(now),
                    }
                )
                return rec
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()

    # ---- 心跳续租 ----
    def heartbeat(self, task_id: str, worker_id: str, lease_secs: int) -> bool:
        """worker 续租；任务确属该 worker 且未终止才续。返回是否成功续租。"""
        lease_until = _iso(self._now() + timedelta(seconds=lease_secs))
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    "UPDATE tasks SET lease_until=?, updated_at=? "
                    "WHERE id=? AND lease_owner=? AND state=?",
                    (lease_until, self._iso_now(), task_id, worker_id, STATE_RUNNING),
                )
                return cur.rowcount > 0
            finally:
                con.close()

    # ---- 崩溃恢复 ----
    def requeue_expired(self, scope: Optional[str] = None) -> int:
        """把租约过期的 running 任务重置为 queued（worker 崩溃恢复，SC-006）。

        返回重排的任务数。scope=None 时作用于全部 scope。
        """
        now = self._iso_now()
        with self._lock:
            con = self._connect()
            try:
                if scope is None:
                    cur = con.execute(
                        "UPDATE tasks SET state=?, lease_owner=NULL, lease_until=?, updated_at=? "
                        "WHERE state=? AND lease_until < ?",
                        (STATE_QUEUED, now, now, STATE_RUNNING, now),
                    )
                else:
                    cur = con.execute(
                        "UPDATE tasks SET state=?, lease_owner=NULL, lease_until=?, updated_at=? "
                        "WHERE scope=? AND state=? AND lease_until < ?",
                        (STATE_QUEUED, now, now, scope, STATE_RUNNING, now),
                    )
                return cur.rowcount
            finally:
                con.close()

    # ---- 终止迁移（canceled 优先，FR-010）----
    def complete(self, task_id: str, worker_id: str) -> bool:
        """标记完成；若已 canceled 则不改写（迟到结果不覆盖）。返回是否迁移。"""
        return self._transition(
            task_id, worker_id, STATE_COMPLETED, blocked_states=(STATE_CANCELED,)
        )

    def fail(self, task_id: str, worker_id: str) -> bool:
        """标记失败；若已 canceled 则不改写。返回是否迁移。"""
        return self._transition(
            task_id, worker_id, STATE_FAILED, blocked_states=(STATE_CANCELED,)
        )

    def _transition(
        self,
        task_id: str,
        worker_id: str,
        target: str,
        blocked_states: tuple,
    ) -> bool:
        with self._lock:
            con = self._connect()
            try:
                placeholders = ",".join("?" * len(blocked_states))
                cur = con.execute(
                    f"UPDATE tasks SET state=?, updated_at=? "
                    f"WHERE id=? AND lease_owner=? AND state NOT IN ({placeholders})",
                    (target, self._iso_now(), task_id, worker_id, *blocked_states),
                )
                return cur.rowcount > 0
            finally:
                con.close()

    def cancel(self, task_id: str) -> bool:
        """取消任务（queued/running → canceled）。返回是否发生迁移。"""
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    "UPDATE tasks SET state=?, updated_at=? "
                    "WHERE id=? AND state IN (?, ?)",
                    (STATE_CANCELED, self._iso_now(), task_id, STATE_QUEUED, STATE_RUNNING),
                )
                return cur.rowcount > 0
            finally:
                con.close()

    def is_canceled(self, task_id: str) -> bool:
        """worker 在返回结果前检查取消状态（FR-010）。"""
        rec = self.get(task_id)
        return rec is not None and rec["state"] == STATE_CANCELED

    # ---- 查询 ----
    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                return self._row_to_dict(row) if row is not None else None
            finally:
                con.close()

    def list(
        self, scope: Optional[str] = None, state: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """按 scope（per-scope 隔离）与状态过滤查询任务（created_at 升序）。"""
        clauses, params = [], []
        if scope is not None:
            clauses.append("scope=?")
            params.append(scope)
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    f"SELECT * FROM tasks {where} ORDER BY created_at", params
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                con.close()


__all__ = [
    "DurableTaskManager",
    "STATE_CANCELED",
    "STATE_COMPLETED",
    "STATE_FAILED",
    "STATE_QUEUED",
    "STATE_RUNNING",
    "queue_db_path",
]
