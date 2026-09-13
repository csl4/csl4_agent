"""Agent 框架共用的数据/值对象（原 core/models.py，拆分为 tool/result/context 三模块）。

对外保持 `from GSagent.core.models import X` 兼容（re-export）。
"""

from GSagent.core.models.context import (
    ApprovalRequirement,
    ContextWindowUsage,
    ToolInvokeContext,
)
from GSagent.core.models.result import (
    ShellResult,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    shell_result_to_structured,
)
from GSagent.core.models.tool import ToolParameter

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
