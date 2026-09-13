"""langchain 工具注册表 + 守卫/审批包装层（002-langchain-ecosystem，contracts/tools.md）。

替代既有 ``Toolset``/``Tool``/``ToolExecutor`` 执行路径。工具以 langchain
``@tool`` 装饰器定义，注册进本注册表；编排经 ``ToolNode`` 或自定义分发执行。
- ``register``：注册 ``BaseTool``（含守卫/审批包装）。
- ``get_tool`` / ``get_all_tools``：查询（bind_tools / 编排分发）。
- ``requires_approval``：工具是否需审批（US2 完善 HITL 语义）。
- 守卫：工具经 ``wrap_with_guards`` 包装，invoke 前执行 PathGuard/CommandGuard。
"""

from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import BaseTool, StructuredTool, tool

from GSagent.core.policy.rules import guard_kind_for, param_name_for


class ToolRegistry:
    """langchain 工具注册表（名字 → BaseTool）。"""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._approval_rules: Dict[str, bool] = {}
        self._approval_checks: Dict[str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = {}
        # 守卫（PathGuard/CommandGuard）与审计：装配时注入
        self.path_guard: Optional[Any] = None
        self.command_guard: Optional[Any] = None
        self.audit_log: Optional[Any] = None
        self.hitl_policy: Optional[Any] = None
        # 会话级已批准前缀（bash/sandbox 动态审批：用户批准后写入 allow 校验）
        self.approved_prefixes: set = set()

    def record_approval(self, prefixes: Optional[list]) -> None:
        """记录已批准前缀（bash/sandbox 审批恢复后，重新校验时放行）。"""
        for p in prefixes or []:
            if isinstance(p, str) and p.strip():
                self.approved_prefixes.add(p.strip())

    def configure(
        self,
        *,
        path_guard: Optional[Any] = None,
        command_guard: Optional[Any] = None,
        audit_log: Optional[Any] = None,
        hitl_policy: Optional[Any] = None,
    ) -> "ToolRegistry":
        """注入守卫/审计/HITL（由 config 装配时调用）。"""
        self.path_guard = path_guard
        self.command_guard = command_guard
        self.audit_log = audit_log
        self.hitl_policy = hitl_policy
        return self

    def register(
        self,
        t: BaseTool,
        requires_approval: bool = False,
        approval_check: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ) -> BaseTool:
        """注册工具（可标记静态审批或动态 approval_check）；自动包装守卫层。

        ``approval_check(args) -> dict | None``：按参数内容动态判审批（如 bash 的
        ``validate_command``），返回审批需求 dict（含 reason/prefixes_to_save）或 None（放行）。
        """
        wrapped = wrap_with_guards(
            t,
            path_guard=self.path_guard,
            command_guard=self.command_guard,
            audit_log=self.audit_log,
        )
        self._tools[wrapped.name] = wrapped
        if requires_approval:
            self._approval_rules[wrapped.name] = True
        if approval_check is not None:
            self._approval_checks[wrapped.name] = approval_check
        return wrapped

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """按名查询工具；未找到返回 None。"""
        return self._tools.get(name)

    def get_all_tools(self) -> List[BaseTool]:
        """全量工具列表（供 bind_tools / ToolNode）。"""
        return list(self._tools.values())

    def approval_requirement(self, name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """返回审批需求 dict（reason/prefixes_to_save）或 None（放行）。

        优先级：hitl=never → 放行；动态 ``approval_check``（如 bash validate_command）→
        按参数内容判；静态标记 + hitl=always → 需审批。
        """
        if self.hitl_policy is not None and self.hitl_policy.never:
            return None
        check = self._approval_checks.get(name)
        if check is not None:
            return check(args)
        if self._approval_rules.get(name, False) or (
            self.hitl_policy is not None and self.hitl_policy.always
        ):
            return {"reason": "requires approval", "prefixes_to_save": None}
        return None

    def requires_approval(self, name: str, args: Dict[str, Any]) -> bool:
        """工具是否需审批（bool 兼容视图）。"""
        return self.approval_requirement(name, args) is not None

    def __len__(self) -> int:
        return len(self._tools)


def wrap_with_guards(
    t: BaseTool,
    *,
    path_guard: Optional[Any],
    command_guard: Optional[Any],
    audit_log: Optional[Any],
) -> BaseTool:
    """守卫包装层：调用前按 ``guard_kind_for`` 分类执行 PathGuard/CommandGuard。

    拦截返回错误文本（成为工具结果）+ 审计 ``tool_call/blocked``。
    不属守卫类（guard_kind_for 返回 None）的工具原样返回。
    """
    kind = guard_kind_for(t.name)
    if kind is None:
        return t
    param = param_name_for(t.name) or ""

    def _audit_blocked(params: Dict[str, Any], reason: str, detail: str) -> None:
        if audit_log is None:
            return
        audit_log.record(
            event_type="tool_call",
            payload={
                "tool": t.name,
                "params": params,
                "reason": reason,
                "detail": detail,
            },
            outcome="blocked",
            session_id="",
        )

    def _guarded(**kwargs: Any) -> Any:
        value = kwargs.get(param, "")
        if isinstance(value, str) and value.strip():
            if kind == "path" and path_guard is not None:
                ok, reason = path_guard.check(value)
                if not ok:
                    _audit_blocked(kwargs, "path_guard", reason)
                    return f"Error: Path blocked by PathGuard: {reason}"
            elif kind == "command" and command_guard is not None:
                ok, reason = command_guard.check(value)
                if not ok:
                    _audit_blocked(kwargs, "command_guard", reason)
                    return f"Error: Command blocked by CommandGuard: {reason}"
        return t.invoke(kwargs)

    return StructuredTool.from_function(
        func=_guarded,
        name=t.name,
        description=t.description,
        args_schema=t.args_schema,
    )


def register_tool(
    registry: ToolRegistry, requires_approval: bool = False
) -> Callable[[Callable], BaseTool]:
    """装饰器工厂：``@tool`` 定义并注册进 registry。"""

    def decorator(fn: Callable) -> BaseTool:
        return registry.register(tool(fn), requires_approval=requires_approval)

    return decorator


__all__ = ["ToolRegistry", "register_tool", "wrap_with_guards"]
