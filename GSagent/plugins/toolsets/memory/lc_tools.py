"""memory 工具集（langchain @tool 版，002-langchain-ecosystem）。

remember / search_memory 迁移为 ``@tool``，复用 ``MemoryStore``（SQLite）执行内核。
配置（db_path/scope/user/max_entries）经工厂闭包注入，不暴露为工具参数。
无守卫（工具名不在 PATH/COMMAND_TOOLS）；免批（approval_required_tools 为空）。
"""

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from GSagent.core.memory.store import DEFAULT_MEMORY_DB, MemoryStore, resolve_scope
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
    """工厂：返回 memory @tool 列表（MemoryStore 闭包注入）。"""
    cfg = _load_config(config)
    store = MemoryStore(path=cfg["db_path"], max_entries=cfg["max_entries"])
    scope_default = cfg["scope"]
    user = cfg["user"]

    @tool
    def remember(content: str, kind: str = "fact", scope: str = "") -> str:
        """Store a long-term memory for the current project scope (cross-session)."""
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}")
        eff_scope = scope.strip() or scope_default
        store.remember(
            scope=eff_scope, content=content, kind=kind, source="agent", user=user
        )
        return f"remembered ({kind}) in scope '{eff_scope}'"

    @tool
    def search_memory(query: str, limit: int = 5, kinds: Optional[str] = None) -> str:
        """Search long-term memory for the current project scope by keywords."""
        kinds_list = None
        if kinds:
            kinds_list = [k.strip() for k in kinds.split(",") if k.strip()]
        results = store.search(
            scope=scope_default, query=query, limit=limit, kinds=kinds_list, user=user
        )
        return str(results)

    return [remember, search_memory]


__all__ = ["create_memory_tools"]
