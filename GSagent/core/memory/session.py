"""会话记忆目录（GSagent/core/memory/session.py）。

SessionMemoryStore：把单个交互会话的短期记忆（session_history = 会话累积消息）
落到磁盘目录 `<root>/<session_id>/`，跨进程/跨会话可恢复。每轮覆盖写
`session_history.json` + `meta.json`（session_id / created_at / updated_at /
message_count / 额外 mode 等）。

语义：
- **用户隔离（004-memory-isolation）**：目录层级 `<root>/<user>/<session_id>/`，
  user 默认系统登录用户名（resolve_user_key + safe_user_dir 编码）；
  不同用户会话互不可见、同 session_id 不冲突；meta.json 记录 user。
- **created_at 保持**：重复 save 保留首次 created_at，只推进 updated_at。
- **失败降级**：目录不可写等 OSError → 记 warning 返回 False，不打断聊天。

记忆词汇（见 memory/）：`session_history` = 短期会话记忆（本文件承载的落盘对象）；
`memory_store` = 长期记忆（SQLite，见 store.py）。
"""

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from GSagent.common import DEFAULT_SESSIONS_DIR
from GSagent.core.memory.user import resolve_user_key, safe_user_dir

logger = logging.getLogger(__name__)

_HISTORY_FILE = "session_history.json"
_META_FILE = "meta.json"


def sessions_dir_path(config: Any) -> Path:
    """从配置解析会话记忆目录根（memory.sessions_dir，空=./.GSagent/memories/sessions）。

    供 CLI（main.py）统一使用（B：单一路径来源）。
    """
    raw = str((config.data.get("memory") or {}).get("sessions_dir") or "")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_SESSIONS_DIR


def _iso(ts: datetime) -> str:
    """UTC datetime → 固定宽 ISO 字符串（毫秒 + Z，字典序即时间序）。"""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.rstrip("Z") + "+00:00")
    except ValueError:
        return None


class SessionMemoryStore:
    """单会话记忆目录：session_history 每轮覆盖写 + meta 追踪（按用户隔离）。"""

    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        user: Optional[str] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.root = Path(root) if root else DEFAULT_SESSIONS_DIR
        self.user = user or resolve_user_key()
        self._clock = clock or _default_clock

    # ---- 路径 ----
    def user_dir(self) -> Path:
        """本用户会话目录根：<root>/<user_key>/（safe_user_dir 编码防穿越）。"""
        return self.root / safe_user_dir(self.user)

    def session_dir(self, session_id: str) -> Path:
        return self.user_dir() / session_id

    # ---- 写入 ----
    def save(
        self,
        session_id: str,
        session_history: Optional[List[Dict[str, Any]]],
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """把会话累积消息 + meta 覆盖写到 <root>/<session_id>/。

        磁盘异常（OSError）→ 记 warning 返回 False（静默降级，不打断聊天）。
        """
        session_dir = self.session_dir(session_id)
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("无法创建会话记忆目录 %s: %s", session_dir, e)
            return False

        now = _iso(self._clock())
        history = session_history or []
        merged: Dict[str, Any] = {
            "session_id": session_id,
            "user": self.user,
            "message_count": len(history),
        }
        if meta:
            merged.update(meta)
        # 核心键优先：created_at 保持首次、updated_at/message_count 用本次
        existing = self._read_meta(session_dir)
        merged["created_at"] = (existing or {}).get("created_at", now)
        merged["updated_at"] = now
        merged["message_count"] = len(history)

        try:
            (session_dir / _HISTORY_FILE).write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (session_dir / _META_FILE).write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("写入会话记忆 %s 失败: %s", session_dir, e)
            return False
        return True

    # ---- 读取 ----
    def load(
        self, session_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        """读取会话的 (meta, session_history)；缺失 → (None, None)。"""
        session_dir = self.session_dir(session_id)
        history_file = session_dir / _HISTORY_FILE
        if not history_file.exists():
            return None, None
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
            meta = self._read_meta(session_dir)
        except (OSError, ValueError) as e:
            logger.warning("读取会话记忆 %s 失败: %s", session_dir, e)
            return None, None
        return meta, history

    def _read_meta(self, session_dir: Path) -> Optional[Dict[str, Any]]:
        meta_file = session_dir / _META_FILE
        if not meta_file.exists():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出本用户已落盘会话（<root>/<user>/ 下有 meta.json 的目录），按 updated_at 降序。"""
        user_root = self.user_dir()
        if not user_root.exists():
            return []
        sessions: List[Dict[str, Any]] = []
        for child in sorted(user_root.iterdir()):
            if not child.is_dir():
                continue
            meta = self._read_meta(child)
            if meta is None:
                continue
            sessions.append(meta)
        sessions.sort(
            key=lambda m: (m.get("updated_at") or "", m.get("session_id") or ""),
            reverse=True,
        )
        return sessions

    # ---- 删除 ----
    def remove(self, session_id: str) -> bool:
        """删除某个会话目录；不存在 → False。"""
        session_dir = self.session_dir(session_id)
        if not session_dir.exists():
            return False
        try:
            shutil.rmtree(session_dir)
        except OSError as e:
            logger.warning("删除会话记忆 %s 失败: %s", session_dir, e)
            return False
        return True

    def purge(self, days: int) -> int:
        """清理 updated_at 早于 now - days 的会话，返回删除条数。"""
        cutoff = self._clock() - timedelta(days=days)
        removed = 0
        for meta in self.list_sessions():
            updated = _parse_iso(meta.get("updated_at"))
            if updated is not None and updated < cutoff:
                if self.remove(meta.get("session_id", "")):
                    removed += 1
        return removed


__all__ = [
    "DEFAULT_SESSIONS_DIR",
    "SessionMemoryStore",
    "sessions_dir_path",
]
