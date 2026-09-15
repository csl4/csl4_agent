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


__all__ = [
    "DEFAULT_MEMORY_DB",
    "MEMORY_KINDS",
    "memory_db_path",
    "resolve_scope",
]
