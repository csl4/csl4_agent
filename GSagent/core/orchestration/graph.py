"""LangGraph StateGraph 组装与编译。

两套图：
- ``build_graph``：既有图（手写 tools_node 审批，002-langchain-ecosystem 契约）。
- ``build_graph_agent``：新图（Phase 1 纯 langgraph）——审批下沉到工具包装器
  （``tools/approval.py``），工具执行用 ``langgraph.prebuilt.ToolNode``，
  checkpointer/store 可注入（SqliteSaver / SqliteStore）。

新图拓扑：
``START → guard_in → agent → should_continue ─┬─ tools(ToolNode) ─ agent（循环）``
                                              └─ guard_out → END

- ``agent`` 后 ``should_continue``：无工具调用/熔断 → guard_out；否则 → tools。
- ``tools`` 内审批/前端暂停由工具包装器 ``interrupt()`` 触发（人在回环），
  恢复用 ``Command(resume={interrupt_id: {...}})``（per-interrupt-id）。
"""

from typing import Any, Callable, Dict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from GSagent.core.observability import AgentEventType
from GSagent.core.observability.telemetry import traced_node
from GSagent.core.orchestration.nodes import (
    _emit,
    _msg,
    agent_node,
    guard_in_node,
    guard_out_node,
    make_should_continue,
)
from GSagent.core.orchestration.state import GraphState
from GSagent.core.tools.approval import wrap_all_with_approval
from GSagent.utils.stream import StreamEvents


def route_after_guard_in(state: Dict[str, Any]) -> str:
    """guard_in 之后：输入被拦截（terminated=blocked）→ 结束；否则进 agent。"""
    return "end" if state.get("terminated") else "agent"


def _make_tool_node(loop: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """新图 tools 节点：ToolNode 执行（审批下沉到工具包装器）。

    发射 START_TOOL / TOOL_RESULT 渲染事件与 TOOL_CALL_START/END 业务事件。
    """
    tools = wrap_all_with_approval(
        loop.tools_registry.get_all_tools(), registry=loop.tools_registry
    )
    inner = ToolNode(tools)

    def _node(state: Dict[str, Any]) -> Dict[str, Any]:
        stream_msgs: list[Dict[str, Any]] = []
        last_calls: list[Dict[str, Any]] = list(state.get("last_tool_calls") or [])
        tool_number: int = int(state.get("tool_number", 0))
        for tc in last_calls:
            tool_number += 1
            stream_msgs.append(
                _msg(
                    StreamEvents.START_TOOL,
                    {
                        "tool_call_id": tc.get("id", ""),
                        "tool_name": tc.get("name", "unknown"),
                        "tool_number": tool_number,
                    },
                )
            )
            _emit(
                loop,
                AgentEventType.TOOL_CALL_START,
                state=state,
                message=f"tool start {tc.get('name', '')}",
                payload={"tool": tc.get("name", ""), "tool_call_id": tc.get("id", "")},
            )
        out = inner.invoke(state)
        tool_msgs = out.get("messages") or []
        known = {t.name for t in loop.tools_registry.get_all_tools()}
        for m in tool_msgs:
            m_name = getattr(m, "name", "")
            stream_msgs.append(
                _msg(
                    StreamEvents.TOOL_RESULT,
                    {
                        "tool_call_id": getattr(m, "tool_call_id", ""),
                        "tool_name": m_name,
                        "content": str(m.content),
                    },
                )
            )
            # 工具缺失 / 返回错误 → TOOL_ERROR 事件（事件流完整性）
            is_err = m_name not in known or str(m.content).startswith("Error")
            _emit(
                loop,
                AgentEventType.TOOL_ERROR if is_err else AgentEventType.TOOL_CALL_END,
                state=state,
                message=f"tool {'error' if is_err else 'end'} {m_name}",
                payload={
                    "tool": m_name,
                    "tool_call_id": getattr(m, "tool_call_id", ""),
                },
            )
        out["tool_number"] = tool_number
        out["last_tool_calls"] = []
        out["_stream_messages"] = stream_msgs
        return out

    return _node


def build_graph_agent(
    loop: Any,
    *,
    checkpointer: Any = None,
    store: Any = None,
) -> Callable[..., Any]:
    """组装并编译新单 Agent 图（Phase 1 纯 langgraph）。

    与 ``build_graph`` 的差异：
    - tools 用 ``ToolNode``（审批已下沉到工具包装器，无手写审批分支）。
    - checkpointer / store 可注入（默认 InMemorySaver / None）。

    参数:
        loop: 提供 LLM/工具/压缩/审计/可观测能力的对象（GraphAgent）。
        checkpointer: langgraph checkpointer（SqliteSaver / InMemorySaver）。
        store: langgraph store（长期记忆，可选）。
    """
    builder = StateGraph(GraphState)
    tracer = getattr(loop, "_tracer", None)
    builder.add_node("guard_in", traced_node(tracer, "guard_in")(guard_in_node(loop)))
    builder.add_node("agent", traced_node(tracer, "agent")(agent_node(loop)))
    builder.add_node("tools", traced_node(tracer, "tools")(_make_tool_node(loop)))
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
    builder.add_edge("tools", "agent")
    builder.add_edge("guard_out", END)

    return builder.compile(
        checkpointer=checkpointer or InMemorySaver(),
        store=store,
    )


__all__ = ["build_graph", "build_graph_agent", "route_after_guard_in"]
