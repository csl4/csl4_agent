"""工具注册表（002-langchain-ecosystem）。

旧 ``Tool``/``Toolset``/``ToolExecutor`` 执行路径已移除（T029/FR-012），工具以
langchain ``@tool`` 装饰器定义并经 ``ToolRegistry`` 注册/查询（含守卫/审批包装）。
"""

from GSagent.core.tools.registry import ToolRegistry, register_tool

__all__ = ["ToolRegistry", "register_tool"]
