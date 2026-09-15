"""核心 Agent 框架。"""

from GSagent.core.models import (
    ApprovalRequirement,
    ContextWindowUsage,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    ToolInvokeContext,
    ToolParameter,
)
from GSagent.core.providers import create_chat_model
from GSagent.core.prompts import PromptComponent
from GSagent.core.truncation import ContextWindowLimiter, SessionCompactor

# 面向图的新执行外壳（纯 langgraph，Phase 5 起 CLI/serve 唯一入口）。
from GSagent.core.agents.graph_agent import GraphAgent, PauseRequest
# 环境适配：跨终端探测/改写/执行（US2 FR-002）。
from GSagent.core.env.terminal import (
    TerminalType,
    adapt_command,
    detect_shell,
    execute_in_shell,
)
# 命令执行统一结果类型（输出接口统一，原 terminal.ShellResult 迁入 models.result）。
from GSagent.core.models.result import ShellResult

__all__ = [
    "ApprovalRequirement",
    "SessionCompactor",
    "ContextWindowLimiter",
    "ContextWindowUsage",
    "GraphAgent",
    "PauseRequest",
    "PromptComponent",
    "ShellResult",
    "StructuredToolResult",
    "StructuredToolResultStatus",
    "TerminalType",
    "ToolCallResult",
    "ToolInvokeContext",
    "ToolParameter",
    "adapt_command",
    "create_chat_model",
    "detect_shell",
    "execute_in_shell",
]
