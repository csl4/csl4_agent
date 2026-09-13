"""工具集实现。在此添加你的工具。

内置 Python 工具集注册在 BUILTIN_PYTHON_TOOLSETS（工具集名 -> 工厂函数的映射）
中，并由 Config.create_tool_executor() 自动加载，它会将 config.yaml 中各自的
配置段（例如 `bash:` 或 `filesystem:`）作为 install_config 传给每个工具集。
直接放在本目录下的 *.yaml / *.yml YAML 工具集文件也会被自动加载。
"""

# ======================= 中文导览 =======================
# 这里是【内置工具集注册中心】：声明哪些工具集可用、以何种工厂构建。
# BUILTIN_PYTHON_TOOLSETS：toolset 名 → 工厂函数的映射；Config.create_tool_executor()
#   会遍历它逐个调用工厂，并传入各自 config.yaml 段（如 `bash:` / `filesystem:`）当 install_config。
# 数据流位置：Config（装配根）从这里拿到内置工具集清单，加载进 ToolExecutor。
# 新增一个 Python 工具集：实现 create_xxx_toolset() 并在此登记即可。
# =========================================================

from typing import Callable, Dict, Optional

from GSagent.core.tools import Toolset
from GSagent.plugins.toolsets.bash import create_bash_toolset
from GSagent.plugins.toolsets.filesystem import create_filesystem_toolset
from GSagent.plugins.toolsets.memory import create_memory_toolset
from GSagent.plugins.toolsets.sandbox import create_sandbox_toolset

# Builtin Python toolset factories, keyed by the config.yaml section that
# configures them. Each takes an optional install_config dict and returns a
# Toolset (or None to skip loading).
BUILTIN_PYTHON_TOOLSETS: Dict[str, Callable[[Optional[Dict]], Optional[Toolset]]] = {
    "filesystem": create_filesystem_toolset,
    "bash": create_bash_toolset,
    "sandbox": create_sandbox_toolset,  # US2：轻量沙箱（FR-003/004）
    "memory": create_memory_toolset,  # 长期记忆：remember / search_memory
} # 工具集
