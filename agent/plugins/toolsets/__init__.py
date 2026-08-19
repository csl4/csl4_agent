"""Toolset implementations. Add your tools here.

Builtin Python toolsets are registered in BUILTIN_PYTHON_TOOLSETS and loaded
automatically by Config.create_tool_executor(). YAML toolsets placed as
*.yaml / *.yml files directly in this directory are also loaded automatically.
"""

from typing import Callable, Dict, Optional

from agent.core.tools import Toolset
from agent.plugins.toolsets.filesystem import create_filesystem_toolset

# Builtin Python toolset factories. Each takes an optional install_config
# dict and returns a Toolset (or None to skip loading).
BUILTIN_PYTHON_TOOLSETS: list[Callable[[Optional[Dict]], Optional[Toolset]]] = [
    create_filesystem_toolset,
]
