"""轻量沙箱工具集（US2：FR-003/004，research.md §4）。

002-langchain-ecosystem 后以 ``@tool`` 定义（``lc_tools.py``）；本包仅保留
配置与工厂导出（旧 ``RunSandboxCommand``/``create_sandbox_toolset`` 已移除，T029）。
"""

from GSagent.plugins.toolsets.sandbox.lc_tools import (
    SandboxExecutorConfig,
    create_sandbox_tools,
)

__all__ = ["SandboxExecutorConfig", "create_sandbox_tools"]
