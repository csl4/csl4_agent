"""本地历史/执行日志存储（FR-008，research.md §6 本地执行日志库）。

JSONL 追加写（无外部服务、无并发锁的 v1 轻量实现）：每条记录是
HistoryRecord 序列化后的单行 JSON。查询按 type + 字段做线性扫描，
记录规模增长后可换 SQLite（见 research.md §5 取舍说明）。
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HISTORY_FILE = Path.home() / ".agent" / "history.jsonl"

# 时间戳统一格式（秒级精度，测试依赖）。
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(_TIMESTAMP_FORMAT)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class HistoryRecord:
    """一条历史记录（command / event / session 等）。"""

    id: str
    type: str
    created_at: str
    session_id: str = ""
    agent_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        type_: str,
        session_id: str = "",
        agent_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> "HistoryRecord":
        return cls(
            id=_new_id(type_),
            type=type_,
            created_at=_now(),
            session_id=session_id,
            agent_id=agent_id,
            payload=dict(payload) if payload else {},
        )

    @classmethod
    def from_json(cls, line: str) -> Optional["HistoryRecord"]:
        """解析单行 JSON；格式非法返回 None（调用方跳过，不中断）。"""
        try:
            data = json.loads(line)
            return cls(
                id=str(data["id"]),
                type=str(data["type"]),
                created_at=str(data["created_at"]),
                session_id=str(data.get("session_id", "")),
                agent_id=str(data.get("agent_id", "")),
                payload=dict(data.get("payload") or {}),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class HistoryStore:
    """JSONL 历史存储：追加写 + 按条件查询（FR-008）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_HISTORY_FILE

    # ---- 写 ----
    def append(self, record: HistoryRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json() + "\n")

    def append_command(
        self,
        command: str,
        session_id: str = "",
        agent_id: str = "",
        shell: str = "",
        status: str = "",
        output: str = "",
        duration_ms: float = 0.0,
        approved: bool = False,
    ) -> HistoryRecord:
        """追加一条命令执行记录（T034 SubAgent 落库用）。"""
        record = HistoryRecord.new(
            "command",
            session_id=session_id,
            agent_id=agent_id,
            payload={
                "command": command,
                "shell": shell,
                "status": status,
                "output": output,
                "duration_ms": duration_ms,
                "approved": approved,
            },
        )
        self.append(record)
        return record

    def append_session(self, session_id: str = "", payload: Optional[Dict[str, Any]] = None) -> HistoryRecord:
        """追加一条会话记录。"""
        record = HistoryRecord.new("session", session_id=session_id, payload=payload)
        self.append(record)
        return record

    # ---- 读 ----
    def _read(self) -> List[HistoryRecord]:
        if not self.path.exists():
            return []
        records: List[HistoryRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = HistoryRecord.from_json(line)
                if record is not None:
                    records.append(record)
        return records

    def query_session(self, session_id: str) -> List[HistoryRecord]:
        return [r for r in self._read() if r.session_id == session_id]

    def query_command(self, pattern: str) -> List[HistoryRecord]:
        """按命令文本子串匹配（不区分大小写）的 command 记录。"""
        needle = pattern.lower()
        return [
            r
            for r in self._read()
            if r.type == "command" and needle in str(r.payload.get("command", "")).lower()
        ]

    def list_recent(self, limit: int = 10) -> List[HistoryRecord]:
        return self._read()[-limit:]


__all__ = [
    "DEFAULT_HISTORY_FILE",
    "HistoryRecord",
    "HistoryStore",
]
