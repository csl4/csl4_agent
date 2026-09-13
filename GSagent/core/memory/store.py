"""长期记忆 SQLite 存储（GSagent/core/memory/store.py）。

MemoryStore：跨会话、按项目 scope 的长期记忆。stdlib `sqlite3`，零新依赖；
连接/模式初始化沿用 `GSagent/core/runtime/tasks.py` 的既有模式
（isolation_level=None + check_same_thread=False + row_factory + executescript）。

关键语义：
- **去重 upsert**：同 user 同 scope 同 content（sha256 content_hash）不重复插入；
  重复 remember 视为强化——刷新 updated_at/last_access_at、递增 access_count，
  并以最新 kind/source 覆盖。
- **per-user 隔离（004-memory-isolation）**：隔离键 = user + scope；
  user 默认系统登录用户名（resolve_user_key()），scope = 项目维度（默认当前工作目录）；
  查询/统计/清理按 user + scope 复合过滤，不同用户记忆互不可见。
- **离线召回**：无 embedding——content 词条重叠打分 + access_count 加权 + recency 加权。
- **配额清理**：prune() 按 last_access_at LRU 清到 max_entries（按 user + scope）。

记忆词汇（见 memory/）：`memory_store` = 长期记忆；`session_history` = 短期会话记忆。
"""

import hashlib
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from GSagent.common import DEFAULT_MEMORY_DB
from GSagent.core.memory.user import resolve_user_key

logger = logging.getLogger(__name__)

# 记忆类型（kind 枚举）：事实 / 偏好 / 约束 / 修正 / 决策
MEMORY_KINDS = ("fact", "preference", "constraint", "correction", "decision")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user           TEXT NOT NULL,
    scope          TEXT NOT NULL,
    kind           TEXT NOT NULL,
    content        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    source         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_access_at TEXT NOT NULL,
    access_count   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user, scope, content_hash)
);
"""

# 索引独立：迁移完成后再建（旧库无 user 列时建索引会报错）
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_memories_scope
    ON memories (user, scope, kind, updated_at);
"""


def _iso(ts: datetime) -> str:
    """UTC datetime → 固定宽 ISO 字符串（毫秒 + Z，字典序即时间序）。"""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def memory_db_path(config: Any) -> Path:
    """从配置解析长期记忆 SQLite 路径（memory.db_path，空=./.GSagent/memory.db）。

    供 CLI（main.py）与 memory 工具集工厂统一使用（B：单一路径来源）。
    """
    db_path = str((config.data.get("memory") or {}).get("db_path") or "")
    if db_path:
        return Path(db_path).expanduser()
    return DEFAULT_MEMORY_DB


def resolve_scope(cfg_scope: str) -> str:
    """解析记忆 scope：配置为空 → 当前工作目录（项目维度）。"""
    return str(cfg_scope or "").strip() or str(Path.cwd().resolve())


def _tokenize(text: str) -> List[str]:
    """把查询拆成词条：中文连续串整体当一个词条，英文按词拆分。"""
    return [t for t in re.findall(r"[A-Za-z0-9]+|[一-鿿]+", text.lower()) if t]


class MemoryStore:
    """SQLite 长期记忆存储（per-scope 隔离 + content_hash 去重 + LRU 配额）。"""

    def __init__(
        self,
        path: Union[str, Path],
        clock: Optional[Callable[[], datetime]] = None,
        max_entries: int = 500,
    ) -> None:
        self.path = str(path)
        self.max_entries = max_entries
        self._clock = clock or _default_clock
        self._lock = threading.RLock()
        self._ensure_schema()

    # ---- 连接 ----
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            # 表定义（IF NOT EXISTS：旧表已存在则跳过，等待迁移）
            con.executescript(_SCHEMA)
            # 迁移检测：既有库（无 user 列）→ 重建表 + 旧数据归属默认用户（R-02）
            cols = [r[1] for r in con.execute("PRAGMA table_info(memories)")]
            if "user" not in cols:
                self._migrate_add_user(con)
            # 索引最后建：迁移后新表必有 user 列
            con.executescript(_INDEXES)
        finally:
            con.close()

    def _migrate_add_user(self, con: sqlite3.Connection) -> None:
        """旧库（无 user 列）迁移：重建表 + 既有记录归属当前系统用户。

        幂等：仅在 _ensure_schema 检测无 user 列时调用。调用方如需回退，
        可在构造前复制 ``<db_path>.bak``（低成本，见 research R-02 遗留风险）。
        """
        default_user = resolve_user_key().replace("'", "''")
        con.executescript(
            "ALTER TABLE memories RENAME TO memories_old;"
            + _SCHEMA
            + f"""
            INSERT INTO memories
                (user, scope, kind, content, content_hash, source,
                 created_at, updated_at, last_access_at, access_count)
            SELECT '{default_user}', scope, kind, content, content_hash, source,
                   created_at, updated_at, last_access_at, access_count
            FROM memories_old;
            DROP TABLE memories_old;
            """
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return dict(row)

    def close(self) -> None:
        # 短连接（每次操作即开即关），无持有连接可关；保留 close 兼容语义。
        return None

    # ---- 写入 ----
    def remember(
        self,
        scope: str,
        content: str,
        kind: str = "fact",
        source: str = "manual",
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """写入/强化一条长期记忆，返回记录 dict。

        同 user 同 scope 同 content（sha256 去重）不重复插入；重复 remember 视为强化。
        user 默认当前系统用户（resolve_user_key()，零迁移向后兼容）。
        """
        content = str(content or "").strip()
        if not content:
            raise ValueError("content 不能为空")
        if kind not in MEMORY_KINDS:
            raise ValueError(f"kind 必须是 {MEMORY_KINDS} 之一，收到 {kind!r}")

        user_key = user or resolve_user_key()
        now = _iso(self._clock())
        h = _content_hash(content)

        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM memories WHERE user = ? AND scope = ? AND content_hash = ?",
                    (user_key, scope, h),
                ).fetchone()
                if row is not None:
                    con.execute(
                        """UPDATE memories
                           SET kind = ?, source = ?, updated_at = ?,
                               last_access_at = ?, access_count = access_count + 1
                           WHERE id = ?""",
                        (kind, source, now, now, row["id"]),
                    )
                    con.commit()
                    rec = self._fetch_by_id(con, user_key, scope, row["id"])
                else:
                    cur = con.execute(
                        """INSERT INTO memories
                           (user, scope, kind, content, content_hash, source,
                            created_at, updated_at, last_access_at, access_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                        (user_key, scope, kind, content, h, source, now, now, now),
                    )
                    con.commit()
                    rec = self._fetch_by_id(con, user_key, scope, cur.lastrowid)
            finally:
                con.close()

            self.prune(scope, user=user_key)
            return rec

    # ---- 查询 ----
    def _fetch_by_id(
        self, con: sqlite3.Connection, user: str, scope: str, memory_id: int
    ) -> Dict[str, Any]:
        row = con.execute(
            "SELECT * FROM memories WHERE user = ? AND scope = ? AND id = ?",
            (user, scope, memory_id),
        ).fetchone()
        return self._row_to_dict(row)

    def list(
        self,
        scope: str,
        kind: Optional[str] = None,
        limit: int = 50,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出 user + scope 下最近更新的记忆（kind 可选过滤，最新在前）。"""
        user_key = user or resolve_user_key()
        con = self._connect()
        try:
            if kind:
                rows = con.execute(
                    "SELECT * FROM memories WHERE user = ? AND scope = ? AND kind = ? "
                    "ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (user_key, scope, kind, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM memories WHERE user = ? AND scope = ? "
                    "ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (user_key, scope, limit),
                ).fetchall()
        finally:
            con.close()
        return [self._row_to_dict(r) for r in rows]

    def search(
        self,
        scope: str,
        query: str,
        limit: int = 10,
        kinds: Optional[List[str]] = None,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """离线关键词召回：user + scope 下 content 词条重叠打分 + access/recency 加权，相关度降序。

        空 query → 回退为最近列表；kinds 可选过滤。
        """
        terms = _tokenize(query)
        if not terms:
            return self.list(scope, limit=limit, user=user)

        user_key = user or resolve_user_key()
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM memories WHERE user = ? AND scope = ?", (user_key, scope)
            ).fetchall()
        finally:
            con.close()

        now = self._clock()
        scored: List[tuple] = []
        for row in rows:
            content = row["content"].lower()
            matched = [t for t in terms if t in content]
            if not matched:
                continue
            if kinds and row["kind"] not in kinds:
                continue
            # 相关度：命中词条数 ×100 + access_count(封顶 10)×10 + recency(≤50)
            score = len(matched) * 100 + min(row["access_count"], 10) * 10
            try:
                updated = datetime.fromisoformat(
                    row["updated_at"].rstrip("Z") + "+00:00"
                )
                age_days = max(0.0, (now - updated).total_seconds() / 86400.0)
                score += max(0, 50 - int(age_days * 5))
            except (ValueError, TypeError):
                pass
            scored.append((score, row["id"], row))

        scored.sort(key=lambda item: (-item[0], -item[1]))
        return [self._row_to_dict(r) for _, _, r in scored[:limit]]

    def stats(self, scope: str, user: Optional[str] = None) -> Dict[str, Any]:
        """user + scope 下记忆统计：总数 + 按 kind 计数。"""
        user_key = user or resolve_user_key()
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT kind, COUNT(*) AS c FROM memories WHERE user = ? AND scope = ? GROUP BY kind",
                (user_key, scope),
            ).fetchall()
        finally:
            con.close()
        by_kind = {r["kind"]: r["c"] for r in rows}
        return {"total": sum(by_kind.values()), "by_kind": by_kind}

    # ---- 删除 ----
    def forget(self, scope: str, memory_id: int, user: Optional[str] = None) -> bool:
        """按 id 删除某条记忆；不存在或跨 user/scope → False。"""
        user_key = user or resolve_user_key()
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    "DELETE FROM memories WHERE user = ? AND scope = ? AND id = ?",
                    (user_key, scope, memory_id),
                )
                con.commit()
                return cur.rowcount > 0
            finally:
                con.close()

    def forget_scope(self, scope: str, user: Optional[str] = None) -> int:
        """清空 user + scope 下所有记忆，返回删除条数。"""
        user_key = user or resolve_user_key()
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    "DELETE FROM memories WHERE user = ? AND scope = ?", (user_key, scope)
                )
                con.commit()
                return cur.rowcount
            finally:
                con.close()

    def prune(self, scope: str, user: Optional[str] = None) -> int:
        """按 last_access_at LRU 淘汰 user + scope 下超出 max_entries 的最旧记忆。"""
        if self.max_entries <= 0:
            return 0
        user_key = user or resolve_user_key()
        with self._lock:
            con = self._connect()
            try:
                # 最新在前（DESC），跳过保留的 max_entries 条，剩下的即最旧待淘汰
                over = con.execute(
                    """SELECT id FROM memories WHERE user = ? AND scope = ?
                       ORDER BY last_access_at DESC, id DESC
                       LIMIT -1 OFFSET ?""",
                    (user_key, scope, self.max_entries),
                ).fetchall()
                if not over:
                    return 0
                ids = [r["id"] for r in over]
                cur = con.executemany(
                    "DELETE FROM memories WHERE id = ?", [(i,) for i in ids]
                )
                con.commit()
                return cur.rowcount
            finally:
                con.close()


__all__ = [
    "DEFAULT_MEMORY_DB",
    "MEMORY_KINDS",
    "MemoryStore",
    "memory_db_path",
    "resolve_scope",
]
