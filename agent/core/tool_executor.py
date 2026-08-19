"""Tool registration, lookup, and execution engine."""

import logging
from typing import Any, Dict, List, Optional

from agent.core.models import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    ToolParameter,
)
from agent.core.tools import Tool, Toolset, ToolsetStatusEnum, ToolsetTag, ToolsetType

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Manages tool registration, lookup, lazy initialization, and execution.

    Supports:
    - Multiple toolset types (YAML, PYTHON, HTTP, MCP)
    - Lazy toolset initialization (prerequisites checked on first use)
    - Name conflict resolution (MCP tools get {toolset}__{tool} prefix)
    - Tag-based toolset filtering (toolset_tag_filter)
    """

    def __init__(
        self,
        toolsets: Optional[List[Toolset]] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
    ):
        """Initialize the ToolExecutor.

        Args:
            toolsets: Initial list of toolsets to register.
            toolset_tag_filter: If provided, only load toolsets that have at least
                one matching tag. None means no filtering (load all enabled toolsets).
                Example: [ToolsetTag.CORE, ToolsetTag.CLI] for CLI mode,
                [ToolsetTag.CORE, ToolsetTag.CLUSTER] for server mode.
        """
        self.toolsets: List[Toolset] = toolsets or []
        self.toolset_tag_filter: Optional[List[ToolsetTag]] = toolset_tag_filter
        self.enabled_toolsets: List[Toolset] = []
        self.tools_by_name: Dict[str, Tool] = {}
        self._tool_to_toolset: Dict[str, Toolset] = {}
        self._initialized_toolsets: set = set()
        self._build_index()

    def _build_index(self) -> None:
        """Rebuild the tool name index from all enabled toolsets.

        Filters toolsets by status (ENABLED) and, if toolset_tag_filter is set,
        by tags. A toolset must have at least one matching tag to pass the filter.
        Toolsets with no tags are excluded when a filter is active.
        """
        self.tools_by_name.clear()
        self._tool_to_toolset.clear()

        # Filter by status first
        enabled = [ts for ts in self.toolsets if ts.status == ToolsetStatusEnum.ENABLED]

        # Apply tag filter if configured
        if self.toolset_tag_filter is not None:
            filtered: List[Toolset] = []
            for ts in enabled:
                if not ts.tags:
                    logger.debug(
                        f"Toolset '{ts.name}' has no tags, skipping "
                        f"(tag filter active: {[t.value for t in self.toolset_tag_filter]})"
                    )
                    continue
                # Check if any toolset tag matches the filter
                if any(tag in self.toolset_tag_filter for tag in ts.tags):
                    filtered.append(ts)
                else:
                    logger.debug(
                        f"Toolset '{ts.name}' tags {[t.value for t in ts.tags]} "
                        f"don't match filter {[t.value for t in self.toolset_tag_filter]}"
                    )
            self.enabled_toolsets = filtered
        else:
            self.enabled_toolsets = enabled

        for toolset in self.enabled_toolsets:
            for tool in toolset.tools:
                name = self._resolve_tool_name(tool.name, toolset)
                if name in self.tools_by_name:
                    logger.warning(
                        f"Tool name conflict: '{name}' already registered. "
                        f"Skipping duplicate from toolset '{toolset.name}'."
                    )
                    continue
                self.tools_by_name[name] = tool
                self._tool_to_toolset[name] = toolset

        logger.info(
            f"Built tool index: {len(self.tools_by_name)} tools from "
            f"{len(self.enabled_toolsets)} toolsets"
            + (
                f" (tag filter: {[t.value for t in self.toolset_tag_filter]})"
                if self.toolset_tag_filter
                else ""
            )
        )

    def _resolve_tool_name(self, tool_name: str, toolset: Toolset) -> str:
        """Resolve tool name, adding MCP prefix to avoid conflicts."""
        if toolset.type == ToolsetType.MCP:
            return f"{toolset.name}__{tool_name}"
        return tool_name

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Look up a tool by name. Returns None if not found."""
        return self.tools_by_name.get(name)

    def get_toolset_name(self, tool_name: str) -> Optional[str]:
        """Get the toolset name that owns a given tool."""
        toolset = self._tool_to_toolset.get(tool_name)
        return toolset.name if toolset else None

    def ensure_toolset_initialized(self, tool_name: str) -> Optional[str]:
        """Lazy-initialize the toolset for a tool name.

        Returns an error message if initialization fails, or None on success.
        """
        toolset = self._tool_to_toolset.get(tool_name)
        if not toolset:
            return f"Tool '{tool_name}' not found in any toolset."

        if toolset.name in self._initialized_toolsets:
            return None

        if not toolset.check_prerequisites():
            toolset.mark_failed()
            self._build_index()
            return f"Toolset '{toolset.name}' prerequisites check failed."

        self._initialized_toolsets.add(toolset.name)
        return None

    def execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        context: Any,
        tool_call_id: str = "",
    ) -> ToolCallResult:
        """Execute a tool by name and return a ToolCallResult.

        Args:
            tool_name: Name of the tool to execute.
            params: Parameters to pass to the tool.
            context: ToolInvokeContext (or compatible) with user_approved, etc.
            tool_call_id: ID of the LLM tool call for correlation.

        Returns:
            ToolCallResult wrapping the StructuredToolResult.
        """
        import time

        start = time.time()

        tool = self.get_tool_by_name(tool_name)
        if tool is None:
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Tool '{tool_name}' not found.",
                ),
            )

        result = tool.invoke(params, context)
        elapsed = (time.time() - start) * 1000

        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
            execution_time_ms=elapsed,
        )

    def get_tools_as_openai(self) -> List[Dict[str, Any]]:
        """Get all registered tools in OpenAI-compatible format."""
        return [tool.to_openai_tool() for tool in self.tools_by_name.values()]

    def add_toolset(self, toolset: Toolset) -> None:
        """Register a new toolset and rebuild the index."""
        self.toolsets.append(toolset)
        self._build_index()