"""环境信息采集：快照当前工作环境并格式化为提示词上下文（FR-007）。

`collect_env_info()` 返回当前 shell / 工作目录 / 平台 / Python 版本，
以及（可选）可用工具列表；`format_env_info()` 渲染为 Agent 注入块
（当前环境信息），让 Agent 在无外部服务的 v1 实现里获得"环境信息库"
能力（research.md §6 本地能力）。
"""

import os
import platform
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from agent.core.env.terminal import detect_shell


@dataclass
class EnvironmentInfo:
    """一次环境采集的快照。"""

    shell: str
    cwd: str
    platform: str
    python_version: str
    available_tools: List[str] = field(default_factory=list)


def collect_env_info(tool_names: Optional[List[str]] = None) -> EnvironmentInfo:
    """采集当前环境快照。

    tool_names 非空时，把它原样记录为 available_tools（调用方负责
    提供当前执行器实际挂载的工具名列表，避免此处耦合 ToolExecutor）。
    """
    return EnvironmentInfo(
        shell=detect_shell().value,
        cwd=os.getcwd(),
        platform=sys.platform,
        python_version=platform.python_version(),
        available_tools=list(tool_names) if tool_names else [],
    )


def format_env_info(info: EnvironmentInfo) -> str:
    """把环境快照格式化为提示词上下文块（FR-007 注入用）。"""
    lines = [
        "当前环境信息:",
        f"- 工作目录: {info.cwd}",
        f"- Shell: {info.shell}",
        f"- 平台: {info.platform}",
        f"- Python: {info.python_version}",
    ]
    if info.available_tools:
        lines.append(f"- 可用工具: {', '.join(info.available_tools)}")
    return "\n".join(lines)


__all__ = ["EnvironmentInfo", "collect_env_info", "format_env_info"]
