"""工具分类规则（002-enterprise-cli-upgrade US1，T013，FR-001/002）。

界定哪些工具属于「路径类」（由 PathGuard 校验 `path` 参数）与
「命令类」（由 CommandGuard 校验 `command` 参数）。

`guard_kind_for()` 是核心引擎的单一决策入口：ToolExecutor 用它决定
对某工具挂哪个守卫，避免策略逻辑散落工具名硬编码（R-01）。
"""

from typing import Dict, Optional

# 工具名 → 路径参数名（PathGuard 校验，FR-001）
PATH_TOOLS: Dict[str, str] = {
    "list_directory": "path",
    "read_file": "path",
    "search_files": "path",
    "file_info": "path",
}

# 工具名 → 命令参数名（CommandGuard 校验，FR-002）
COMMAND_TOOLS: Dict[str, str] = {
    "bash": "command",
    "sandbox": "command",
}


def guard_kind_for(tool_name: str) -> Optional[str]:
    """返回工具归属的守卫类别：``"path"`` / ``"command"`` / ``None``（不守卫）。"""
    if tool_name in PATH_TOOLS:
        return "path"
    if tool_name in COMMAND_TOOLS:
        return "command"
    return None


def param_name_for(tool_name: str) -> Optional[str]:
    """返回守卫校验的参数名（``path`` 或 ``command``），不守卫时为 None。"""
    return PATH_TOOLS.get(tool_name) or COMMAND_TOOLS.get(tool_name)


__all__ = [
    "COMMAND_TOOLS",
    "PATH_TOOLS",
    "guard_kind_for",
    "param_name_for",
]
