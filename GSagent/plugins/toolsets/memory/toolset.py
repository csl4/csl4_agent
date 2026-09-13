"""长期记忆工具集（GSagent/plugins/toolsets/memory/toolset.py）。

把 MemoryStore 暴露成 `remember` / `search_memory` 两个 agent 工具：agent 可在
对话中主动持久化事实，并在需要时召回跨会话的长期记忆。

store 实例挂在 Toolset.config 上，工具经 context.toolset.config.store 读写
（与 filesystem 工具集的 `_get_config` 同款）。

install_config 来自 Config.create_tool_executor 的 `self.data.get("memory")`
（即 config `memory:` 段）：db_path / scope / max_entries。
"""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict

from GSagent.core.memory.store import DEFAULT_MEMORY_DB, MemoryStore, resolve_scope
from GSagent.core.memory.user import resolve_user_key
from GSagent.core.models import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)
from GSagent.core.tools import Tool, Toolset, ToolsetTag, ToolsetType
from GSagent.utils.pydantic_utils import ToolsetConfig

MEMORY_KINDS_JSON = ["fact", "preference", "constraint", "correction", "decision"]


class MemoryToolsetConfig(ToolsetConfig):
    """memory 工具集配置：store 实例 + 默认 scope + 用户维度（004-memory-isolation）。"""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    store: Optional[MemoryStore] = None
    scope: str = "default"
    user: str = ""  # 用户维度；空=自动系统用户（resolve_user_key）
    db_path: str = ""
    max_entries: int = 500


def _get_config(context: ToolInvokeContext) -> MemoryToolsetConfig:
    """从调用上下文取工具集配置（context 无 config 时回退空配置）。"""
    toolset = getattr(context, "toolset", None)
    if toolset is not None and toolset.config is not None:
        return toolset.config
    return MemoryToolsetConfig()


class RememberTool(Tool):
    """记住一条长期记忆（跨会话，当前项目 scope）。"""

    name: str = "remember"
    description: str = (
        "Store a fact/preference/constraint/correction/decision into "
        "long-term memory. Persists across sessions for the current project "
        "scope. Repeating the same content does not duplicate it, but "
        "strengthens the existing entry."
    )

    parameters: Dict[str, ToolParameter] = {
        "content": ToolParameter(
            type="string",
            description="The memory content to persist (one clear sentence).",
            required=True,
        ),
        "kind": ToolParameter(
            type="string",
            description="Memory kind.",
            required=False,
            default="fact",
            enum=MEMORY_KINDS_JSON,
        ),
        "scope": ToolParameter(
            type="string",
            description="Project scope override (default: toolset scope).",
            required=False,
            default="",
        ),
    }

    def _invoke(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> StructuredToolResult:
        cfg = _get_config(context)
        if cfg.store is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="memory 工具集未初始化（缺少 MemoryStore）",
                params=params,
            )
        content = str(params.get("content") or "").strip()
        if not content:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="content 不能为空",
                params=params,
            )
        kind = str(params.get("kind") or "fact")
        scope = str(params.get("scope") or "").strip() or cfg.scope
        try:
            rec = cfg.store.remember(
                scope=scope, content=content, kind=kind, source="agent", user=cfg.user
            )
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        # access_count==0 → 新插入；>0 → 重复强化
        status_label = "updated" if rec["access_count"] > 0 else "saved"
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "status": status_label,
                "memory_id": rec["id"],
                "scope": scope,
                "kind": rec["kind"],
                "content": rec["content"],
            },
            params=params,
        )


class SearchMemoryTool(Tool):
    """按关键词召回当前项目 scope 的长期记忆。"""

    name: str = "search_memory"
    description: str = (
        "Search long-term memory for the current project scope. Returns the "
        "most relevant remembered facts for a query, most relevant first. "
        "Use this to recall decisions, constraints, or preferences the user "
        "or you persisted earlier."
    )

    parameters: Dict[str, ToolParameter] = {
        "query": ToolParameter(
            type="string",
            description="Keywords to recall from long-term memory (empty = latest).",
            required=True,
        ),
        "limit": ToolParameter(
            type="integer",
            description="Max results to return.",
            required=False,
            default=5,
        ),
        "kinds": ToolParameter(
            type="array",
            description="Optional memory kinds to filter (fact/preference/...).",
            required=False,
            default=None,
            items=ToolParameter(type="string"),
        ),
    }

    def _invoke(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> StructuredToolResult:
        cfg = _get_config(context)
        if cfg.store is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="memory 工具集未初始化（缺少 MemoryStore）",
                params=params,
            )
        query = str(params.get("query") or "")
        try:
            limit = int(params.get("limit") or 5)
        except (ValueError, TypeError):
            limit = 5
        kinds = self._coerce_kinds(params.get("kinds"))
        try:
            results = cfg.store.search(
                scope=cfg.scope, query=query, limit=limit, kinds=kinds, user=cfg.user
            )
        except Exception as e:  # noqa: BLE001 - 工具层容错
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"搜索记忆失败: {e}",
                params=params,
            )
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "scope": cfg.scope,
                "query": query,
                "count": len(results),
                "results": results,
            },
            params=params,
        )

    @staticmethod
    def _coerce_kinds(raw: Any) -> Optional[List[str]]:
        if raw is None:
            return None
        if isinstance(raw, str):
            return [k.strip() for k in raw.split(",") if k.strip()] or None
        if isinstance(raw, list):
            return [str(k).strip() for k in raw if str(k).strip()] or None
        return None


def create_memory_toolset(
    install_config: Optional[Dict[str, Any]] = None,
) -> Toolset:
    """创建长期记忆工具集（remember / search_memory）。

    参数:
        install_config: 可选配置覆盖（config `memory:` 段），如
            {"db_path": "...", "scope": "proj", "max_entries": 500}。

    返回:
        带 MemoryStore 的 Toolset；未配置时用默认 db 路径与当前工作目录 scope。
    """
    cfg = install_config or {}
    db_path = str(cfg.get("db_path") or "").strip() or str(DEFAULT_MEMORY_DB)
    scope = resolve_scope(str(cfg.get("scope") or ""))
    # 用户维度：config memory.user（空=自动系统用户）
    user = resolve_user_key(str(cfg.get("user") or ""))
    try:
        max_entries = int(cfg.get("max_entries") or 500)
    except (ValueError, TypeError):
        max_entries = 500

    store = MemoryStore(path=db_path, max_entries=max_entries)
    config = MemoryToolsetConfig(
        store=store, scope=scope, user=user, db_path=db_path, max_entries=max_entries
    )

    return Toolset(
        name="memory",
        description=(
            "Long-term memory: remember facts/preferences/decisions and "
            "search them across sessions for the current project scope."
        ),
        tools=[RememberTool(), SearchMemoryTool()],
        config=config,
        type=ToolsetType.PYTHON,
        tags=[ToolsetTag.CORE],
    )
