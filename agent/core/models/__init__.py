"""Agent 框架共用的数据/值对象（原 core/models.py，拆分为 tool/result/context 三模块）。

对外保持 `from agent.core.models import X` 兼容（re-export）。
"""

from agent.core.models.context import (
    ApprovalRequirement,
    ContextWindowUsage,
    ToolInvokeContext,
)
from agent.core.models.result import (
    ShellResult,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    shell_result_to_structured,
)
from agent.core.models.tool import ToolParameter

__all__ = [
    "ApprovalRequirement",
    "ContextWindowUsage",
    "ShellResult",
    "StructuredToolResult",
    "StructuredToolResultStatus",
    "ToolCallResult",
    "ToolInvokeContext",
    "ToolParameter",
    "shell_result_to_structured",
]
