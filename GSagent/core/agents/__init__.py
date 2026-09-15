"""Agent 外壳：面向图的新执行入口（纯 langgraph 重构）。

``GraphAgent`` 是 CLI/serve 的唯一执行入口：编译并暴露 LangGraph 图
（单 Agent / 多 Agent / Plan），``stream()`` 产出 ``StreamMessage``（渲染）
与 ``PauseRequest``（审批暂停，per-interrupt-id resume）。
"""

from GSagent.core.agents.graph_agent import GraphAgent, PauseRequest

__all__ = ["GraphAgent", "PauseRequest"]
