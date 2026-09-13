"""调用上下文与元数据（值对象）：token 用量、审批请求、工具调用上下文。"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---- 值对象：token 用量统计 ----
# 输入：LLM provider 填充；输出：供压缩判定/界面显示。
# 企业级（US2 T020，R-06）：cache_read/cache_write/reasoning 明细随
# model_call 审计事件记录（contracts/audit.md usage 键名一致），成本按本地
# 定价表估算。字段带默认值 → 既有构造（total_tokens=1 等）零迁移。
class ContextWindowUsage(BaseModel):
    """用于上下文窗口管理的 token 用量统计。"""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning_tokens: int = 0


# ---- 值对象：审批请求 ----
# 输入：Tool.requires_approval() 返回（工具自述「我要人批准」）；
# 输出：主循环遇此即暂停并 yield APPROVAL_REQUIRED 事件。
class ApprovalRequirement(BaseModel):
    """由 Tool.requires_approval() 返回的审批需求。

    当工具判断其在执行前需要人工审批时，会返回该对象。主循环将暂停，
    产出 APPROVAL_REQUIRED 事件，等待用户的决策。
    """

    needs_approval: bool = False
    reason: str = ""
    tool_name: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    # Prefixes to persist when the user approves (bash toolset allow-listing).
    prefixes_to_save: List[str] = Field(default_factory=list)


# ---- 值对象：调用上下文 + 污点追踪核心载体 ----
# 输入：主循环 / ToolExecutor 在每次调用前构造（带 user_approved、tool_call_id 等）；
# 输出：随 Tool.invoke() 一路传给 _invoke()，工具靠它读取环境与资格。
# 设计理念：把「是否可信」内化为运行时 flag，而非散落在各工具手写判断里。
#   user_approved=False（默认，脏）：参数来自 LLM，执行前需审批/校验；
#   user_approved=True （干净）：已过人工批准，可跳过校验直接执行。
# 安全细节：model_dump()/__str__ 会脱敏 request_context，防止敏感头泄入日志。
class ToolInvokeContext(BaseModel):
    """工具调用上下文 —— 污点追踪（taint tracking）的核心载体。

    关键设计：`user_approved` 是污点追踪的中心状态标志：
    - False：工具调用参数来自 LLM（受污染），需要完整校验
    - True：工具调用已获人工批准（已净化），可跳过校验

    `request_context` 字段在序列化时会自动脱敏，防止敏感请求头泄漏到日志。
    """

    user_approved: bool = False
    llm: Optional[Any] = None
    max_token_count: int = 8000
    tool_call_id: str = ""
    tool_name: str = ""
    session_approved_prefixes: List[str] = Field(default_factory=list)
    request_context: Optional[Dict[str, Any]] = None
    toolset: Optional[Any] = None

    class Config:
        """ToolInvokeContext 的 Pydantic 配置。"""

        arbitrary_types_allowed = True

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        """序列化时对 request_context 脱敏，防止敏感请求头泄漏。"""
        data = super().model_dump(**kwargs)
        if "request_context" in data and data["request_context"] is not None:
            data["request_context"] = "<redacted>"
        return data

    def __str__(self) -> str:
        """对 request_context 脱敏后的字符串表示。"""
        data = self.model_dump()
        return f"ToolInvokeContext({data})"


__all__ = [
    "ApprovalRequirement",
    "ContextWindowUsage",
    "ToolInvokeContext",
]
