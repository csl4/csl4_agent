"""YAML 工具集加载器（langchain 版，002-langchain-ecosystem）。

把 ``*.yaml`` 声明的命令模板工具加载为 langchain ``StructuredTool``：
- 复用 YAML 语义：参数经 ``shlex.quote`` 防注入 → Jinja2 渲染命令模板。
- 动态参数 schema：按 YAML 的 parameters 定义构建 pydantic model。
- ``load_yaml_toolsets_lc(directory) -> list[BaseTool]`` 供 ToolRegistry 注册。
"""

import logging
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Template
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field as PField
from pydantic import create_model

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _build_args_schema(params_def: Dict[str, Any]) -> Any:
    """按 YAML parameters 定义构建 pydantic args schema。"""
    fields: Dict[str, Any] = {}
    for name, pdef in (params_def or {}).items():
        ptype = _TYPE_MAP.get(pdef.get("type", "string"), str)
        required = bool(pdef.get("required", False))
        kwargs: Dict[str, Any] = {"description": pdef.get("description", "")}
        if not required:
            kwargs["default"] = pdef.get("default")
        fields[name] = (ptype, PField(**kwargs))
    return create_model("YamlToolArgs", **fields)


def _make_yaml_tool(tool_def: Dict[str, Any]) -> BaseTool:
    """把单个 YAML 工具定义转为 StructuredTool。"""
    template = tool_def.get("command", "")
    args_schema = _build_args_schema(tool_def.get("parameters", {}))

    def _run(**kwargs: Any) -> str:
        sanitized = {k: shlex.quote(str(v)) for k, v in kwargs.items()}
        try:
            return Template(template).render(**sanitized)
        except Exception as e:  # noqa: BLE001 - 渲染失败转错误文本
            return f"Error: Failed to render command template: {e}"

    return StructuredTool.from_function(
        func=_run,
        name=tool_def["name"],
        description=tool_def.get("description", ""),
        args_schema=args_schema,
    )


def load_yaml_toolsets_lc(directory: Path) -> List[BaseTool]:
    """从目录加载全部 YAML 工具集为 langchain 工具列表。"""
    if not directory.exists():
        logger.debug(f"YAML toolset directory not found: {directory}")
        return []

    tools: List[BaseTool] = []
    seen: set = set()
    for pattern in ("*.yaml", "*.yml"):
        for yaml_file in sorted(directory.glob(pattern)):
            stem = yaml_file.stem
            if stem in seen:
                continue
            seen.add(stem)
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                for tool_def in data.get("tools", []):
                    try:
                        tools.append(_make_yaml_tool(tool_def))
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Failed to parse YAML tool in {yaml_file}: {e}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to load YAML toolset from {yaml_file}: {e}")
    return tools


__all__ = ["load_yaml_toolsets_lc"]
