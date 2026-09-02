"""核心 Agent 框架。"""

from agent.core.llm import LLM, LiteLLMProvider, ModelResponse
from agent.core.models import (
    ApprovalRequirement,
    ContextWindowUsage,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    ToolInvokeContext,
    ToolParameter,
)
from agent.core.prompt_components import PromptComponent
from agent.core.tool_calling_llm import ToolCallingLLM
from agent.core.tool_executor import ToolExecutor
from agent.core.tools import (
    CallablePrerequisite,
    Prerequisite,
    Tool,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
    ToolsetType,
    Transformer,
)
from agent.core.transformers import JsonTruncationTransformer, LineCountTransformer
from agent.core.truncation import ContextWindowLimiter, SessionCompactor

# 多Agent：协议、通信客户端与四类角色（宪法 I：插件/模块化，不侵入单 Agent 主循环）。
from agent.core.a2a.client import A2AClient, A2AClientError, InProcessA2AClient
from agent.core.agents import (
    AgentRole,
    BaseAgent,
    BusinessAgent,
    MainAgent,
    Orchestrator,
    SubAgent,
    merge_results,
    run_task_safe,
)

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
    "StructuredToolResult",
    "StructuredToolResultStatus",
    "SubAgent",
    "Tool",
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
    "merge_results",
    "run_task_safe",
]
