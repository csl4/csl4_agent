"""审批下沉包装器（纯 langgraph 重构，Phase 1）。

把审批语义从编排 tools 节点搬进工具包装器，使任何 ``ToolNode`` / ``create_agent``
都能复用同一套「人在回环」审批，不再依赖手写 tools_node 的审批分支。

设计（GSDOC langgraph.graph §3，langgraph 1.x interrupt 语义）：
- 工具执行前/中触发 ``interrupt()``，图暂停并向客户端暴露 ``__interrupt__``
  （每个 Interrupt 带唯一 ``id``）。
- 恢复用 ``Command(resume={interrupt_id: {"approved": bool}})`` —— 每个待审批
  调用独立匹配，天然支持 ToolNode 并行执行多个工具时的批量审批。
- 守卫（PathGuard/CommandGuard）仍在注册层 ``wrap_with_guards`` 提供；
  本包装器是**审批层**，叠加在已守卫工具之上。

两层审批：
1. 规则级（invoke 前）：``registry.approval_requirement`` —— 静态标记 + HITL 模式。
2. 动态级（invoke 后）：工具返回信号 dict（bash/sandbox ``validate_command`` 三态，
   即 ``__denied__`` / ``__approval_required__`` / ``__frontend_pause__``）。
"""

from typing import Any, Callable, Dict, Optional

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import interrupt


def _is_approved(resumed: Any) -> bool:
    """从 interrupt 恢复值提取审批决策（``{"approved": True}``）。"""
    if not isinstance(resumed, dict):
        return False
    return bool(resumed.get("approved", False))


def _interrupt_approval(
    tool_name: str,
    params: Dict[str, Any],
    reason: str,
    prefixes_to_save: Optional[list],
) -> Any:
    """触发审批 interrupt 并返回恢复值（用户决策）。"""
    return interrupt(
        {
            "type": "approval",
            "tool_name": tool_name,
            "params": params,
            "reason": reason or "requires approval",
            "prefixes_to_save": list(prefixes_to_save or []),
        }
    )


def wrap_with_approval(
    t: BaseTool,
    *,
    registry: Any,
) -> BaseTool:
    """审批层包装（叠加在已守卫工具之上）。

    - invoke 前：规则级审批（``registry.approval_requirement``）。
    - invoke 后：动态信号审批（bash/sandbox validate_command 三态）与前端暂停。
    - 拒绝返回错误文本（成为 ToolMessage 内容）；批准后执行并把批准前缀记入 registry。
    """

    def _approved(**kwargs: Any) -> Any:
        # 1) 规则级审批（invoke 前）
        req = registry.approval_requirement(t.name, kwargs)
        if req is not None:
            if req.get("denied"):
                content = f"Error: {req.get('reason') or 'command denied'}"
                return content
            resumed = _interrupt_approval(
                t.name,
                kwargs,
                req.get("reason") or "",
                req.get("prefixes_to_save"),
            )
            if not _is_approved(resumed):
                return "User denied approval for this tool call."
            registry.record_approval(req.get("prefixes_to_save"))

        # 2) 执行
        raw = t.invoke(kwargs)

        # 3) 动态信号处理
        if not isinstance(raw, dict):
            return str(raw) if not isinstance(raw, str) else raw
        if raw.get("__frontend_pause__"):
            resumed = interrupt(
                {
                    "type": "frontend",
                    "tool_name": t.name,
                    "params": kwargs,
                }
            )
            frontend_results = (resumed or {}).get("frontend_tool_results") or {}
            return str(frontend_results.get("value", frontend_results))
        if raw.get("__denied__"):
            return f"Error: {raw['__denied__']}"
        if raw.get("__approval_required__"):
            info = raw["__approval_required__"] or {}
            resumed = _interrupt_approval(
                t.name,
                kwargs,
                info.get("reason") or "",
                info.get("prefixes_to_save"),
            )
            if not _is_approved(resumed):
                return "User denied approval for this tool call."
            registry.record_approval(info.get("prefixes_to_save"))
            # 批准前缀已记入，重新执行（validate_command 放行）
            re_raw = t.invoke(kwargs)
            if isinstance(re_raw, dict):
                if re_raw.get("__denied__"):
                    return f"Error: {re_raw['__denied__']}"
                if re_raw.get("__approval_required__"):
                    return f"Error: approval re-required after approval ({t.name})"
            return str(re_raw) if not isinstance(re_raw, str) else re_raw
        return str(raw) if not isinstance(raw, str) else raw

    return StructuredTool.from_function(
        func=_approved,
        name=t.name,
        description=t.description,
        args_schema=t.args_schema,
    )


def wrap_all_with_approval(
    tools: list[BaseTool],
    *,
    registry: Any,
) -> list[BaseTool]:
    """批量审批包装（供新图 / create_agent workers 构建工具列表用）。"""
    return [wrap_with_approval(t, registry=registry) for t in tools]


__all__ = ["wrap_with_approval", "wrap_all_with_approval"]
