"""YAML toolset loader — loads toolset definitions from YAML files.

Supports the YAML toolset format:
    name: my_toolset
    description: My toolset
    type: YAML
    tags: [CLI]
    tools:
      - name: my_command
        description: Run a command
        parameters:
          command:
            type: string
            description: The command to run
            required: true
        command: "{{ command }}"

Parameters are sanitized via shlex.quote() before being inserted into
Jinja2 command templates.
"""

import logging
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Template

from agent.core.tools import Tool, Toolset, ToolsetTag, ToolsetType
from agent.core.models import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)

logger = logging.getLogger(__name__)


class YamlTool(Tool):
    """A tool defined in a YAML toolset file.

    Executes a Jinja2 command template with sanitized parameters.
    """

    command_template: str = ""

    def _invoke(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> StructuredToolResult:
        """Render the command template with sanitized parameters.

        Args:
            params: Tool parameters (already type-coerced by Tool.invoke()).
            context: Tool invocation context.

        Returns:
            StructuredToolResult with the rendered command string.
        """
        # Sanitize parameters with shlex.quote() for shell safety
        sanitized: Dict[str, str] = {}
        for key, value in params.items():
            sanitized[key] = shlex.quote(str(value))

        try:
            template = Template(self.command_template)
            rendered = template.render(**sanitized)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data={"command": rendered},
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to render command template: {e}",
                params=params,
            )


def _parse_tool_parameter(param_def: Dict[str, Any]) -> ToolParameter:
    """Parse a tool parameter definition from YAML.

    Args:
        param_def: Parameter definition dict from YAML.

    Returns:
        ToolParameter instance.
    """
    return ToolParameter(
        type=param_def.get("type", "string"),
        description=param_def.get("description", ""),
        required=param_def.get("required", False),
        default=param_def.get("default"),
        enum=param_def.get("enum"),
    )


def _parse_tool(tool_def: Dict[str, Any]) -> YamlTool:
    """Parse a single tool definition from YAML.

    Args:
        tool_def: Tool definition dict from YAML.

    Returns:
        YamlTool instance.
    """
    parameters: Dict[str, ToolParameter] = {}
    for name, param_def in tool_def.get("parameters", {}).items():
        parameters[name] = _parse_tool_parameter(param_def)

    return YamlTool(
        name=tool_def["name"],
        description=tool_def.get("description", ""),
        parameters=parameters,
        command_template=tool_def.get("command", ""),
    )


def load_yaml_toolset(file_path: Path) -> Optional[Toolset]:
    """Load a toolset from a YAML file.

    Args:
        file_path: Path to the YAML file.

    Returns:
        Toolset instance, or None if loading fails.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load YAML toolset from {file_path}: {e}")
        return None

    if not data or not isinstance(data, dict):
        logger.warning(f"Empty or invalid YAML toolset: {file_path}")
        return None

    name = data.get("name", file_path.stem)
    description = data.get("description", "")
    tags_raw = data.get("tags", [])
    tools_raw = data.get("tools", [])

    # Parse tags
    tags: List[ToolsetTag] = []
    for tag_str in tags_raw:
        try:
            tags.append(ToolsetTag(tag_str.upper()))
        except ValueError:
            logger.warning(f"Unknown toolset tag '{tag_str}' in {file_path}")

    # Parse tools
    tools: List[Tool] = []
    for tool_def in tools_raw:
        try:
            tool = _parse_tool(tool_def)
            tools.append(tool)
        except Exception as e:
            logger.warning(f"Failed to parse tool in {file_path}: {e}")

    return Toolset(
        name=name,
        description=description,
        tools=tools,
        type=ToolsetType.YAML,
        tags=tags,
        approval_required_tools=data.get("approval_required_tools", []),
    )


def load_yaml_toolsets(directory: Path) -> List[Toolset]:
    """Load all YAML toolsets from a directory.

    Scans for *.yaml and *.yml files in the given directory.

    Args:
        directory: Path to the directory containing YAML toolset files.

    Returns:
        List of loaded Toolset instances.
    """
    if not directory.exists():
        logger.debug(f"YAML toolset directory not found: {directory}")
        return []

    toolsets: List[Toolset] = []
    for yaml_file in sorted(directory.glob("*.yaml")):
        toolset = load_yaml_toolset(yaml_file)
        if toolset:
            toolsets.append(toolset)
            logger.info(f"Loaded YAML toolset '{toolset.name}' from {yaml_file}")

    for yml_file in sorted(directory.glob("*.yml")):
        if yml_file.stem.endswith(".yaml"):  # Already loaded
            continue
        toolset = load_yaml_toolset(yml_file)
        if toolset:
            toolsets.append(toolset)
            logger.info(f"Loaded YAML toolset '{toolset.name}' from {yml_file}")

    return toolsets