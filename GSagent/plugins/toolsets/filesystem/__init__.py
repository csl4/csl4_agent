"""文件系统工具集——在沙箱根目录内的本地文件操作（langchain @tool 版）。

002-langchain-ecosystem 后以 ``@tool`` 定义（``lc_tools.py``）；旧
``create_filesystem_toolset``（Toolset 路径）已移除（T029）。
"""

from GSagent.plugins.toolsets.filesystem.lc_tools import create_filesystem_tools

__all__ = ["create_filesystem_tools"]
