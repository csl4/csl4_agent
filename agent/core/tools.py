"""Tool and Toolset base classes for the agent plugin system."""

import fnmatch
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from agent.core.models import (
    ApprovalRequirement,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)


class Transformer(ABC, BaseModel):
    """Base class for tool result transformers."""

    @abstractmethod
    def transform(self, result: StructuredToolResult) -> StructuredToolResult:
        """Transform a tool result. Called after _invoke() succeeds."""
        ...


class ToolsetType(str, Enum):
    """Types of toolset implementations."""

    YAML = "YAML"
    PYTHON = "PYTHON"
    HTTP = "HTTP"
    MCP = "MCP"


class ToolsetStatusEnum(str, Enum):
    """Toolset lifecycle status."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    FAILED = "FAILED"


class ToolsetTag(str, Enum):
    """Tags for categorizing toolsets."""

    CORE = "CORE"
    CLUSTER = "CLUSTER"
    CLI = "CLI"


class Prerequisite(BaseModel):
    """Base class for toolset prerequisites."""

    name: str
    description: str = ""

    def check(self, config: Any) -> bool:
        """Check if the prerequisite is met. Override in subclasses."""
        return True


class CallablePrerequisite(Prerequisite):
    """A prerequisite backed by a callable function."""

    callable: Callable[[Any], bool]

    def check(self, config: Any) -> bool:
        """Execute the callable to check the prerequisite."""
        try:
            return self.callable(config)
        except Exception:
            return False


class Tool(ABC, BaseModel):
    """Abstract base class for all tools.

    Subclasses implement _invoke() to provide the actual tool logic.
    The invoke() template method handles approval, coercion, and transformers.
    """

    name: str
    description: str
    parameters: Dict[str, ToolParameter] = Field(default_factory=dict)
    transformers: Optional[List[Transformer]] = None

    class Config:
        """Pydantic config for Tool."""

        arbitrary_types_allowed = True

    def invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        """Template method for tool invocation.

        Order:
        1. Approval check (if not user_approved)
        2. Coerce parameter types
        3. Call _invoke()
        4. Apply transformers (on SUCCESS)
        5. Return result
        """
        # 1. Approval check — if params are tainted (from LLM), verify approval
        if not context.user_approved:
            context.tool_name = self.name
            approval = self.requires_approval(params, context)
            if approval and approval.needs_approval:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                    params=params,
                    error=approval.reason,
                )

        # 2. Coerce parameter types
        coerced = self._coerce_params(params)

        # 3. Execute the tool
        try:
            result = self._invoke(coerced, context)
        except Exception as e:
            result = StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Tool execution failed: {e}",
                params=coerced,
            )

        # 4. Apply transformers on success
        if result.status == StructuredToolResultStatus.SUCCESS and self.transformers:
            for transformer in self.transformers:
                result = transformer.transform(result)

        return result

    @abstractmethod
    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        """Subclasses implement the actual tool logic here."""
        ...

    def requires_approval(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """Tool-specific approval check.

        Default implementation enforces the toolset's approval_required_tools
        patterns (fnmatch globs against the tool name). Subclasses can override
        this for finer-grained logic - e.g. a Bash tool might only require
        approval for dangerous commands, an HTTP tool for non-GET requests.
        """
        toolset = getattr(context, "toolset", None)
        if toolset is None:
            return None

        for pattern in (toolset.approval_required_tools or []):
            if fnmatch.fnmatch(self.name, pattern):
                return ApprovalRequirement(
                    needs_approval=True,
                    reason=f"Tool '{self.name}' matches approval pattern '{pattern}'.",
                    tool_name=self.name,
                    params=params,
                )
        return None

    def _coerce_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce parameter values to their declared types."""
        coerced: Dict[str, Any] = {}
        for name, param_def in self.parameters.items():
            if name not in params:
                if param_def.default is not None:
                    coerced[name] = param_def.default
                continue

            value = params[name]
            if param_def.type == "integer":
                try:
                    coerced[name] = int(value)
                except (ValueError, TypeError):
                    coerced[name] = value
            elif param_def.type == "number":
                try:
                    coerced[name] = float(value)
                except (ValueError, TypeError):
                    coerced[name] = value
            elif param_def.type == "boolean":
                if isinstance(value, str):
                    coerced[name] = value.lower() in ("true", "1", "yes")
                else:
                    coerced[name] = bool(value)
            else:
                coerced[name] = value

        return coerced

    def to_openai_tool(self) -> Dict[str, Any]:
        """Convert to OpenAI-compatible tool definition."""
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for name, param in self.parameters.items():
            prop: Dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default

            properties[name] = prop
            if param.required:
                required.append(name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class Toolset(BaseModel):
    """A collection of related tools with shared configuration and prerequisites."""

    name: str
    description: str
    tools: List[Tool] = Field(default_factory=list)
    prerequisites: List[Prerequisite] = Field(default_factory=list)
    config: Optional[Any] = None
    approval_required_tools: List[str] = Field(default_factory=list)
    tags: List[ToolsetTag] = Field(default_factory=list)
    type: Optional[ToolsetType] = None
    status: ToolsetStatusEnum = ToolsetStatusEnum.ENABLED

    class Config:
        """Pydantic config for Toolset."""

        arbitrary_types_allowed = True

    def check_prerequisites(self) -> bool:
        """Check if all prerequisites are met."""
        for prereq in self.prerequisites:
            if not prereq.check(self.config):
                return False
        return True

    def mark_failed(self) -> None:
        """Mark this toolset as failed."""
        self.status = ToolsetStatusEnum.FAILED