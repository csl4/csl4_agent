"""Shared Pydantic models for the agent framework."""

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ToolParameter(BaseModel):
    """JSON Schema parameter definition for a tool."""

    type: str = "string"
    description: str = ""
    required: bool = False
    default: Optional[Any] = None
    enum: Optional[List[str]] = None


class StructuredToolResultStatus(str, Enum):
    """Possible states of a tool execution result."""

    SUCCESS = "success"
    ERROR = "error"
    NO_DATA = "no_data"
    APPROVAL_REQUIRED = "approval_required"
    FRONTEND_PAUSE = "frontend_pause"


class StructuredToolResult(BaseModel):
    """Result of a tool invocation, with status and optional data/error."""

    status: StructuredToolResultStatus
    data: Any = None
    error: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dictionary for serialization."""
        result: Dict[str, Any] = {"status": self.status.value}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return result


class ToolCallResult(BaseModel):
    """Wraps a StructuredToolResult with metadata for LLM message formatting."""

    tool_call_id: str
    tool_name: str
    result: StructuredToolResult
    execution_time_ms: float = 0.0

    def to_llm_message(self) -> Dict[str, Any]:
        """Format as an LLM-compatible tool result message."""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.tool_name,
            "content": self._format_content(),
        }

    def _format_content(self) -> str:
        """Format the result content for the LLM."""
        if self.result.status == StructuredToolResultStatus.SUCCESS:
            import json

            return json.dumps(self.result.data, ensure_ascii=False, default=str)
        elif self.result.status == StructuredToolResultStatus.ERROR:
            return f"Error: {self.result.error}"
        elif self.result.status == StructuredToolResultStatus.NO_DATA:
            return "No data returned."
        elif self.result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
            return f"Approval required for tool '{self.tool_name}'."
        else:
            return "Tool execution paused, waiting for frontend."


class ContextWindowUsage(BaseModel):
    """Token usage statistics for context window management."""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ApprovalRequirement(BaseModel):
    """Approval requirement returned by Tool.requires_approval().

    When a tool determines that it needs human approval before execution,
    it returns this object. The agent loop will pause and yield an
    APPROVAL_REQUIRED event, waiting for the user's decision.
    """

    needs_approval: bool = False
    reason: str = ""
    tool_name: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)


class ToolInvokeContext(BaseModel):
    """Tool invocation context — the core carrier of taint tracking.

    Key design: `user_approved` is the central state flag for taint tracking:
    - False: tool call parameters come from LLM (tainted), need full validation
    - True: tool call has been approved by human (cleaned), can skip validation

    The `request_context` field is automatically redacted in serialization
    to prevent sensitive headers from leaking into logs.
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
        """Pydantic config for ToolInvokeContext."""

        arbitrary_types_allowed = True

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        """Serialize, redacting request_context to prevent sensitive header leaks."""
        data = super().model_dump(**kwargs)
        if "request_context" in data and data["request_context"] is not None:
            data["request_context"] = "<redacted>"
        return data

    def __str__(self) -> str:
        """String representation with request_context redacted."""
        data = self.model_dump()
        return f"ToolInvokeContext({data})"