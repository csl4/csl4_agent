"""Agent 执行入口（拆壳后：直接消费 langgraph 原生流）。

``AgentRuntime`` 是 CLI/serve 的装配产物（编译图 + 横切能力数据包）；
``run_graph_session`` 是会话运行辅助，yield 原生 ``(mode, chunk)`` 元组
（custom 渲染事件 / updates 含 ``__interrupt__`` 审批暂停）。
"""

from GSagent.core.agents.runtime import AgentRuntime, run_graph_session

__all__ = ["AgentRuntime", "run_graph_session"]
