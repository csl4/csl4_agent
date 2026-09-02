"""面向 Agent 的插件系统。"""

from agent.plugins.interfaces import DestinationPlugin, SourcePlugin

__all__ = [
    "DestinationPlugin",
    "SourcePlugin",
]
