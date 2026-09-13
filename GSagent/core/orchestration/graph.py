"""LangGraph StateGraph 组装与编译。

拓扑（contracts/orchestration.md §2）：
``START → guard_in → agent → should_continue ─┬─ tools ──┬─ agent（循环）``
                                              │           └─ end → guard_out → END
                                              └─ end → guard_out → END

- ``agent`` 后 ``should_continue``：无工具调用/熔断/暂停 → end；否则 → tools。
- ``tools`` 后 ``route_after_tools``：产生暂停（approval/frontend）→ end；否则回 agent 循环。
- checkpointer：InMemorySaver 编译（图规范要求）；暂停恢复靠**下次 call_stream
  以初始状态携带决策重入**，不依赖 checkpoint 恢复语义（R-03）。
"""

from typing import Any, Callable, Dict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from GSagent.core.observability.telemetry import traced_node
from GSagent.core.orchestration.nodes import (
    agent_node,
    guard_in_node,
    guard_out_node,
    make_should_continue,
    tools_node,
)
from GSagent.core.orchestration.state import GraphState


def route_after_guard_in(state: Dict[str, Any]) -> str:
    """guard_in 之后：输入被拦截（terminated=blocked）→ 结束；否则进 agent。"""
    return "end" if state.get("terminated") else "agent"


def build_graph(loop: Any) -> Callable[..., Any]:
    """组装并编译编排图。

    参数:
        loop: ToolCallingLLM 实例（节点复用其 LLM/工具执行/审计/压缩等能力）。

    返回:
        编译好的 LangGraph CompiledStateGraph。消费方式：
        ``graph.stream(initial_state, config, stream_mode="updates")``
        （见 ToolCallingLLM.call_stream 适配器）。
    """
    builder = StateGraph(GraphState)

    # 手动业务 span（方案 A + 手动叠加，contracts/observability.md §2）：
    # loop 提供 _tracer 时节点包一层 node.<name> span；未启用则透传零开销。
    tracer = getattr(loop, "_tracer", None)
    builder.add_node("guard_in", traced_node(tracer, "guard_in")(guard_in_node(loop)))
    builder.add_node("agent", traced_node(tracer, "agent")(agent_node(loop)))
    builder.add_node("tools", traced_node(tracer, "tools")(tools_node(loop)))
    builder.add_node("guard_out", traced_node(tracer, "guard_out")(guard_out_node(loop)))

    builder.add_edge(START, "guard_in")
    builder.add_conditional_edges(
        "guard_in",
        route_after_guard_in,
        {"agent": "agent", "end": "guard_out"},
    )
    builder.add_conditional_edges(
        "agent",
        make_should_continue(loop),
        {"tools": "tools", "end": "guard_out"},
    )
    builder.add_edge("tools", "agent")  # interrupt 由 tools 内部处理暂停，正常返回回 agent
    builder.add_edge("guard_out", END)

    return builder.compile(checkpointer=InMemorySaver())


__all__ = ["build_graph", "route_after_tools"]
