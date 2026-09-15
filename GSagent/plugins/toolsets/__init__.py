"""工具集实现。在此添加你的工具。

002-langchain-ecosystem 后工具以 langchain ``@tool`` 装饰器定义（各工具集
``lc_tools.py``），经 ``Config.create_tools_registry()`` 注册进 ``ToolRegistry``；
旧 ``BUILTIN_PYTHON_TOOLSETS``/``create_xxx_toolset``（Toolset 执行路径）
已移除（T029）。直接放在本目录下的 *.yaml / *.yml YAML 工具集文件由
``yaml_lc_loader.load_yaml_toolsets_lc`` 自动加载。
"""
