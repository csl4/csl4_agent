"""Bash 工具集——基于前缀校验的命令执行（Windows 上的 Git Bash）。

002-langchain-ecosystem 后以 ``@tool`` 定义（``lc_tools.py``）；本包仅保留
配置与校验层导出（旧 ``RunBashCommand``/``create_bash_toolset`` 已移除，T029）。
"""

from GSagent.plugins.toolsets.bash.common.config import BashExecutorConfig
from GSagent.plugins.toolsets.bash.validation import (
    validate_command,
)

__all__ = [
    "BashExecutorConfig",
    "validate_command",
]
