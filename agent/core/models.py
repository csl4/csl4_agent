"""Agent 框架共用的 Pydantic 模型。"""


# ======================= 中文导览 =======================
# 本文件是「值对象 / 盒子」的家：只装数据、被各方传递，本身不含业务逻辑。
# 数据流位置：主循环 <-> 工具执行之间的数据载体。
# 三个易混入口对象的「输入 → 输出」：
#   ToolInvokeContext    (129) → 调用前由主循环构造（传入：执行环境/资格/污点标记）
#   StructuredToolResult (  31) → 工具 _invoke() 的产出本体（发出：执行结果状态+数据）
#   ToolCallResult       (  55) → ToolExecutor 外包一层（结果 + tool_call_id + 耗时；
#                                  负责转成 LLM 能读的 role:"tool" 消息）
# =========================================================


from enum import Enum
from typing import Any, Dict, List, Optional, Union

import json

from pydantic import BaseModel, Field


# ---- 值对象：单个工具参数的「类型定义」----
# 用途：描述一个工具参数的长相（类型/描述/是否必填/默认值/枚举/数组元素）。
# 谁创建：工具类在定义 parameters 字段时写死；谁消费：tool_calling_llm / to_openai_tool()。
class ToolParameter(BaseModel):
    """工具的 JSON Schema 参数定义。"""

    type: str = "string"
    description: str = ""
    required: bool = False
    default: Optional[Any] = None
    enum: Optional[List[str]] = None
    # For type="array": the schema of each element (e.g. ToolParameter(type="string")).
    items: Optional["ToolParameter"] = None


# ---- 值对象：工具执行结果「状态」枚举 ----
# 决定主循环如何对待该结果：回填对话 / 暂停等审批 / 暂停等前端。
class StructuredToolResultStatus(str, Enum):
    """工具执行结果的可能状态。"""

    SUCCESS = "success"
    ERROR = "error"
    NO_DATA = "no_data"
    APPROVAL_REQUIRED = "approval_required"
    FRONTEND_PAUSE = "frontend_pause"


# ---- 值对象：工具执行结果的「本体」----
# 输入：工具 _invoke() 构造并返回；输出：作为 StructuredToolResult 在系统中传递。
# 设计要点：status 决定语义（成功/失败/无数据/审批/前端暂停），data/error 携带载荷。
# 它【不】携带 tool_call_id —— 那是 ToolCallResult 的职责（适配层分离）。
class StructuredToolResult(BaseModel):
    """一次工具调用的结果，包含状态以及可选的 data/error。"""

    status: StructuredToolResultStatus
    data: Any = None
    error: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    # The concrete invocation (e.g. the executed command string), for display.
    invocation: Optional[str] = None
    # Exit code for command-executing tools, if applicable.
    return_code: Optional[int] = None
    # Prefixes the user approved (bash toolset); persisted on approval.
    prefixes_to_save: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典以便序列化。"""
        result: Dict[str, Any] = {"status": self.status.value}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return result


# ---- 值对象：一次「工具调用」的完整记录 / LLM 适配器 ----
# 输入：ToolExecutor 执行完工具后把它包一层（加 tool_call_id、tool_name、耗时）；
# 输出：to_llm_message() 生成 LLM 需要的 role:"tool" 消息，可回填对话。
# 设计要点：与 StructuredToolResult 分离的核心原因 —— 工具是引擎无关的（谁都不认识），
#           而「结果回给哪个 tool_call_id、LLM 要什么格式」是协议层的事，故外包成这一层。
class ToolCallResult(BaseModel):
    """用供 LLM 消息格式化所需的元数据包装 StructuredToolResult。"""

    tool_call_id: str
    tool_name: str
    result: StructuredToolResult
    execution_time_ms: float = 0.0

    def to_llm_message(self) -> Dict[str, Any]:
        """格式化为 LLM 兼容的工具结果消息。"""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.tool_name,
            "content": self._format_content(),
        }

    def _format_content(self) -> str:
        """格式化结果内容以供 LLM 使用。"""
        if self.result.status == StructuredToolResultStatus.SUCCESS:
            # Plain string payloads (e.g. bash output "cmd\nstdout") are passed
            # through as-is: json.dumps would escape every newline/quote into
            # one unreadable line and waste tokens.
            if isinstance(self.result.data, str):
                return self.result.data
            return json.dumps(self.result.data, ensure_ascii=False, default=str)
        elif self.result.status == StructuredToolResultStatus.ERROR:
            return f"Error: {self.result.error}"
        elif self.result.status == StructuredToolResultStatus.NO_DATA:
            return "No data returned."
        elif self.result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
            return f"Approval required for tool '{self.tool_name}'."
        else:
            return "Tool execution paused, waiting for frontend."


# ---- 值对象：token 用量统计 ----
# 输入：LLM provider 填充；输出：供压缩判定/界面显示。
class ContextWindowUsage(BaseModel):
    """用于上下文窗口管理的 token 用量统计。"""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


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