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
from GSagent.core.providers import LLM, LiteLLMProvider, ModelResponse
from GSagent.core.prompts import PromptComponent
from GSagent.core.tools import (
    CallablePrerequisite,
    Prerequisite,
    Tool,
    ToolExecutor,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
    ToolsetType,
    Transformer,
)
from GSagent.core.transformers import JsonTruncationTransformer, LineCountTransformer
from GSagent.core.truncation import ContextWindowLimiter, SessionCompactor

# 多Agent：协议、通信客户端与四类角色（宪法 I：插件/模块化，不侵入单 Agent 主循环）。
from GSagent.core.a2a.client import A2AClient, A2AClientError, InProcessA2AClient
from GSagent.core.agents import (
    AgentRole,
    BaseAgent,
    BusinessAgent,
    MainAgent,
    Orchestrator,
    SubAgent,
    ToolCallingLLM,
    merge_results,
    run_task_safe,
)
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
    "A2AClient",
    "A2AClientError",
    "AgentRole",
    "ApprovalRequirement",
    "BaseAgent",
    "BusinessAgent",
    "CallablePrerequisite",
    "SessionCompactor",
    "ContextWindowLimiter",
    "ContextWindowUsage",
    "InProcessA2AClient",
    "JsonTruncationTransformer",
    "LineCountTransformer",
    "LLM",
    "LiteLLMProvider",
    "MainAgent",
    "ModelResponse",
    "Orchestrator",
    "Prerequisite",
    "PromptComponent",
    "ShellResult",
    "StructuredToolResult",
    "StructuredToolResultStatus",
    "SubAgent",
    "TerminalType",
    "Tool",
    "adapt_command",
    "ToolCallResult",
    "ToolCallingLLM",
    "ToolExecutor",
    "ToolInvokeContext",
    "ToolParameter",
    "Toolset",
    "ToolsetStatusEnum",
    "ToolsetTag",
    "ToolsetType",
    "Transformer",
    "detect_shell",
    "execute_in_shell",
    "merge_results",
    "run_task_safe",
]
