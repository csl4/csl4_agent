"""编排层（LangGraph）：显式图编排 Agent 主流程。

宪法 2.1 五层架构中的 Orchestrator 层。LangGraph StateGraph 以显式节点+条件边
驱动 Agent 主循环，替代手写生成器；外部通过 ``ToolCallingLLM`` 外壳保持
``call_stream()``/``run_task()`` 契约不变（contracts/orchestration.md）。

模块组成（本实现落地于 specs/001-langgraph-otel-refactor）：
- ``state.py``  — GraphState 状态 schema（消息/暂停/熔断/事件缓冲）
- ``graph.py``  — StateGraph 组装与编译（节点注册 + 条件边 + checkpointer）
- ``nodes.py``  — 节点实现（agent / tools / compact / guard_in / guard_out）
"""

__all__: list = []
