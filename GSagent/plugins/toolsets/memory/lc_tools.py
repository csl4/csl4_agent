"""memory 工具集（langchain @tool 版，纯 langgraph 重构）。

remember / search_memory 用 langgraph ``InjectedStore`` 原生注入：store 由图编译
时 ``compile(store=...)`` 提供（graph context 自动注入），不再经工厂闭包持有——
工具与存储解耦，任何 compile(store) 的图（单 Agent/多 Agent/Plan/serve）都可用。
配置（scope/user/max_entries）仍经工厂闭包注入（非运行时用户输入）。
无守卫（工具名不在 PATH/COMMAND_TOOLS）；免批。
"""

from typing import Annotated, Any, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore

from GSagent.core.memory.langgraph_store import StoreMemoryAdapter
from GSagent.core.memory.store import DEFAULT_MEMORY_DB, resolve_scope
from GSagent.core.memory.user import resolve_user_key

VALID_KINDS = ("fact", "preference", "constraint", "correction", "decision")


def _load_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = config or {}
    db_path = str(cfg.get("db_path") or "").strip() or str(DEFAULT_MEMORY_DB)
    scope = resolve_scope(str(cfg.get("scope") or ""))
    user = resolve_user_key(str(cfg.get("user") or ""))
    try:
        max_entries = int(cfg.get("max_entries") or 500)
    except (TypeError, ValueError):
        max_entries = 500
    return {
        "db_path": db_path,
        "scope": scope,
        "user": user,
        "max_entries": max_entries,
    }


def create_memory_tools(config: Optional[Dict[str, Any]] = None) -> List[Any]:
    """工厂：返回 memory @tool 列表（store 经 ``InjectedStore`` 图注入）。"""
    cfg = _load_config(config)
    scope_default = cfg["scope"]
    user = cfg["user"]
    max_entries = cfg["max_entries"]

    @tool
    def remember(
        content: str,
        kind: str = "fact",
        scope: str = "",
        store: Annotated[BaseStore, InjectedStore] = None,
    ) -> str:
        """Store a long-term memory for the current project scope (cross-session)."""
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}")
        eff_scope = scope.strip() or scope_default
        StoreMemoryAdapter(store, max_entries=max_entries).remember(
            scope=eff_scope, content=content, kind=kind, source="agent", user=user
        )
        return f"remembered ({kind}) in scope '{eff_scope}'"

    @tool
    def search_memory(
        query: str,
        limit: int = 5,
        kinds: Optional[str] = None,
        store: Annotated[BaseStore, InjectedStore] = None,
    ) -> str:
        """Search long-term memory for the current project scope by keywords."""
        kinds_list = None
        if kinds:
            kinds_list = [k.strip() for k in kinds.split(",") if k.strip()]
        results = StoreMemoryAdapter(store).search(
            scope=scope_default, query=query, limit=limit, kinds=kinds_list, user=user
        )
        return str(results)

    return [remember, search_memory]


__all__ = ["create_memory_tools"]
