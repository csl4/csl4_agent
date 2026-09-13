"""工具执行结果（值对象）：状态、本体、Shell 结果与 LLM 适配器。"""


# ======================= 中文导览 =======================
# 本文件是「值对象 / 盒子」的家：只装数据、被各方传递，本身不含业务逻辑。
# 数据流位置：主循环 <-> 工具执行之间的数据载体。
# 三个易混入口对象的「输入 → 输出」：
#   StructuredToolResult (  31) → 工具 _invoke() 的产出本体（发出：执行结果状态+数据）
#   ToolCallResult       (  55) → ToolExecutor 外包一层（结果 + tool_call_id + 耗时；
#                                  负责转成 LLM 能读的 role:"tool" 消息）
#   ShellResult          ( 121) → 命令执行（bash/sandbox/terminal）的底层结果盒子；
#                                  经 shell_result_to_structured 转成 StructuredToolResult。
# =========================================================


import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


# ---- 值对象：一次 shell 命令执行的底层结果 ----
# bash / sandbox / terminal 三处命令执行共用同一结果类型（输出接口统一）：
#   stdout 拿到什么输出 → shell_result_to_structured 拼进 StructuredToolResult.data。
#   return_code：None 表示超时被强杀（无正常退出码）；timed_out=True 与之对应。
@dataclass
class ShellResult:
    """一次 shell 执行的结果（bash / sandbox / terminal 统一输出类型）。"""

    stdout: str
    return_code: Optional[int]
    timed_out: bool


# ---- 适配器：ShellResult → StructuredToolResult ----
# 唯一命令结果转换器：bash / sandbox / terminal 三处的命令结果统一走这里变成
# StructuredToolResult（规范输出：一套状态/错误/数据语义，绝不各写各的）。
def shell_result_to_structured(
    result: ShellResult, cmd: str, timeout: int, params: dict
) -> StructuredToolResult:
    """
    将 ShellResult 转换为 StructuredToolResult。

    参数:
        result: 来自命令执行的 ShellResult
        cmd: 原始命令(用于错误消息)
        timeout: 超时值(用于错误消息)
        params: 要包含在结果中的参数

    返回:
        适用于工具响应的 StructuredToolResult
    """
    if result.timed_out:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Error: Command '{cmd}' timed out after {timeout} seconds.",
            data=f"{cmd}\n{result.stdout}" if result.stdout else None,
            params=params,
            invocation=cmd,
        )

    result_data = f"{cmd}\n{result.stdout}"

    if result.return_code == 0:
        status = (
            StructuredToolResultStatus.SUCCESS
            if result.stdout
            else StructuredToolResultStatus.NO_DATA
        )
        error = None
    else:
        status = StructuredToolResultStatus.ERROR
        error = (
            f'Error: Command "{cmd}" returned non-zero exit status {result.return_code}'
        )

    return StructuredToolResult(
        status=status,
        error=error,
        data=result_data,
        params=params,
        invocation=cmd,
        return_code=result.return_code,
    )


__all__ = [
    "ShellResult",
    "StructuredToolResult",
    "StructuredToolResultStatus",
    "ToolCallResult",
    "shell_result_to_structured",
]
