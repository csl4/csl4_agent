"""持久化 checkpointer / store 工厂（纯 langgraph 重构，Phase 2）。

- ``create_saver(path)``：SqliteSaver（会话/断点恢复的 thread_id 维度持久化）。
- ``create_store(path)``：SqliteStore（长期记忆底层，随 langgraph-checkpoint-sqlite
  提供）。isolation_level=None（autocommit）避免与 BaseStore 内部事务冲突；
  check_same_thread=False 支持 serve 多线程。

默认路径约定：checkpoints 与 memory store 分开（checkpoints.db / memory_store.db），
均落在 ``./.GSagent/``。
"""

import sqlite3
from pathlib import Path
from typing import Optional, Union

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore

from GSagent.common import DEFAULT_AGENT_DIR

DEFAULT_CHECKPOINT_DB = str(DEFAULT_AGENT_DIR / "checkpoints.db")
DEFAULT_STORE_DB = str(DEFAULT_AGENT_DIR / "memory_store.db")


def _connect(path: Union[str, Path]) -> sqlite3.Connection:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)


def create_saver(path: Optional[Union[str, Path]] = None) -> SqliteSaver:
    """构造 SqliteSaver（持久化 checkpointer）。空 path → ``./.GSagent/checkpoints.db``。"""
    return SqliteSaver(_connect(path or DEFAULT_CHECKPOINT_DB))


def create_store(path: Optional[Union[str, Path]] = None) -> BaseStore:
    """构造 SqliteStore（持久化长期记忆 store）。空 path → ``./.GSagent/memory_store.db``。"""
    return SqliteStore(_connect(path or DEFAULT_STORE_DB))


__all__ = ["DEFAULT_CHECKPOINT_DB", "DEFAULT_STORE_DB", "create_saver", "create_store"]
