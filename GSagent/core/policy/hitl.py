"""HITL 审批模式策略（002-enterprise-cli-upgrade US1，FR-003/004，R-02）。

三态审批策略 + 可注入的 ``approval_callback``：

- ``auto``（默认）：沿用既有审批语义（toolset pattern + 工具内
  ``requires_approval`` 双检查），行为与旧版一致；
- ``always``：所有工具调用都需要审批；提供 ``approval_callback`` 时
  交由回调同步裁决，否则由调用方决定（交互环境走审批暂停、非交互
  环境拒绝 approver=none）；
- ``never``：免审批直跑（守卫 PathGuard/CommandGuard 仍拦截，FR-002）。

`approve()` 返回三值语义：``True``=已放行 / ``False``=明确拒绝 /
``None``=交由调用方继续既有流程（auto，或 always-无回调）。
"""

from typing import Any, Callable, Dict, Optional

VALID_MODES: tuple = ("auto", "always", "never")

# 回调签名：approval_callback(tool_name, params) -> bool（True=批准）
ApprovalCallback = Callable[[str, Dict[str, Any]], bool]


class HitlPolicy:
    """HITL 三态审批策略对象（单策略入口，FR-003/004）。"""

    def __init__(
        self,
        mode: str = "auto",
        approval_callback: Optional[ApprovalCallback] = None,
    ) -> None:
        self.set_mode(mode)
        self.approval_callback = approval_callback

    # ---- 模式管理 ----
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def auto(self) -> bool:
        return self._mode == "auto"

    @property
    def always(self) -> bool:
        return self._mode == "always"

    @property
    def never(self) -> bool:
        return self._mode == "never"

    def set_mode(self, mode: str) -> None:
        """切换审批模式（/hitl 运行时切换入口，T015）。"""
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid hitl_mode: {mode!r}. Must be one of {VALID_MODES}."
            )
        self._mode = mode

    # ---- 决策 ----
    def approve(self, tool_name: str, params: Dict[str, Any]) -> Optional[bool]:
        """返回审批裁决：True=放行 / False=拒绝 / None=交由调用方继续。

        - ``never`` → True（免审批；守卫仍拦截）
        - ``always`` → 有回调则回调裁决；无回调返回 None（交互暂停 /
          非交互拒绝，由调用方按上下文决定，FR-004 0 静默放行）
        - ``auto`` → None（沿用既有 requires_approval 流程）
        """
        if self._mode == "never":
            return True
        if self._mode == "always":
            if self.approval_callback is not None:
                return bool(self.approval_callback(tool_name, params))
            return None
        return None


__all__ = ["VALID_MODES", "ApprovalCallback", "HitlPolicy"]
