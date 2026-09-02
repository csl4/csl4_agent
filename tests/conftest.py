"""共享 pytest 夹具（自动发现，无需显式导入）。"""

import pytest

from agent.core.tool_executor import ToolExecutor
from agent.core.tools import ToolsetTag
from agent.plugins.toolsets.bash.bash_toolset import create_bash_toolset


@pytest.fixture
def bash_executor() -> ToolExecutor:
    """带 extended 白名单的真实 bash toolset（df/du 免审批直跑）。"""
    toolset = create_bash_toolset({"builtin_allowlist": "extended"})
    return ToolExecutor(toolsets=[toolset], toolset_tag_filter=[ToolsetTag.CLI])
