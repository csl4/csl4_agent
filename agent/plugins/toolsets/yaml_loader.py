"""YAML 工具集加载器——从 YAML 文件加载工具集定义。

支持 YAML 工具集格式:
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

参数在插入 Jinja2 命令模板之前会通过 shlex.quote() 进行净化处理。
"""

# ======================= 中文导览 =======================
# YAML 工具集加载器：把 *.yaml 文件里声明的命令模板工具，加载成 Toolset。
#   YamlTool → 一种「命令模板工具」：参数经 shlex.quote() 安全转义后，塞进 Jinja2 模板渲染成命令字符串。
#   load_yaml_toolsets(dir) → 扫描目录内 *.yaml/*.yml，逐个解析成 Toolset。
# 设计要点：与 Python 工具(list_directory 等)走【同一套 Tool.invoke() 模板方法】——
#           只是 _invoke() 被实现成「渲染命令模板」。所以 YAML 工具也能享受审批/强转/transformer。
# =========================================================

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


# ---- 行为对象：YAML 命令模板工具 ----
# 输入：params（已被基类强转）+ context；输出：StructuredToolResult（含渲染好的 command 字符串）。
# 设计要点：_invoke() 用 shlex.quote() 转义每个参数防 shell 注入，再渲染 Jinja2 模板。
class YamlTool(Tool):
    """定义在 YAML 工具集文件中的工具。

    用净化后的参数执行 Jinja2 命令模板。
    """

    command_template: str = ""

    def _invoke(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> StructuredToolResult:
        """用净化后的参数渲染命令模板。

        参数:
            params: 工具参数（已由 Tool.invoke() 完成类型强转）。
            context: 工具调用上下文。

        返回:
            携带渲染后命令字符串的 StructuredToolResult。
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
    """解析来自 YAML 的工具参数定义。

    参数:
        param_def: 来自 YAML 的参数定义字典。

    返回:
        ToolParameter 实例。
    """
    return ToolParameter(
        type=param_def.get("type", "string"),
        description=param_def.get("description", ""),
        required=param_def.get("required", False),
        default=param_def.get("default"),
        enum=param_def.get("enum"),
    )


def _parse_tool(tool_def: Dict[str, Any]) -> YamlTool:
    """解析来自 YAML 的单个工具定义。

    参数:
        tool_def: 来自 YAML 的工具定义字典。

    返回:
        YamlTool 实例。
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
    """从 YAML 文件加载工具集。

    参数:
        file_path: YAML 文件的路径。

    返回:
        Toolset 实例，加载失败时返回 None。
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
    """从目录中加载所有 YAML 工具集。

    扫描给定目录中的 *.yaml 和 *.yml 文件。

    参数:
        directory: 包含 YAML 工具集文件的目录路径。

    返回:
        已加载的 Toolset 实例列表。
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