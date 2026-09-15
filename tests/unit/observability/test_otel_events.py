"""可观测事件流测试：langchain 化后事件真实随执行产生 + 指标聚合。

FakeChatLLM 离线打桩。验证（SC-001/002，FR-001/002/008）：
- call_stream 执行后事件流含 AGENT_START/LLM_RESPONSE/AGENT_END
- 事件携带 token，MetricsAggregator 聚合正确
- 未接入 OTel 时 trace_id 留空（FR-010）
- 工具调用产生 TOOL_CALL_START / TOOL_ERROR
"""

from langchain_core.messages import AIMessage

from GSagent.core.agents.graph_agent import GraphAgent
from GSagent.core.observability import AgentEventType, MetricsAggregator
from GSagent.core.tools.registry import ToolRegistry
from tests.helpers import FakeChatLLM


def _agent(chat_model):
    return GraphAgent(
        chat_model=chat_model,
        tools_registry=ToolRegistry(),
        max_steps=5,
        enable_compaction=False,
    )


def _usage(p=3, c=2):
    return {
        "token_usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    }


def _tool_call(name="nope_tool", tc_id="c1", args=None):
    return {"name": name, "args": args or {}, "id": tc_id, "type": "tool_call"}


class TestObservabilityEvents:
    def test_lifecycle_and_llm_events_emitted(self):
        """call_stream 后事件流含 AGENT_START/LLM_RESPONSE/AGENT_END。"""
        llm = FakeChatLLM(
            responses=[AIMessage(content="hi", response_metadata=_usage())]
        )
        agent = _agent(llm)
        list(
            agent.stream(
                messages=[{"role": "user", "content": "x"}],
                session_id="s1",
            )
        )
        events = agent.event_emitter.store.query(session_id="s1")
        types = {e.event_type for e in events}
        assert AgentEventType.AGENT_START in types
        assert AgentEventType.LLM_RESPONSE in types
        assert AgentEventType.AGENT_END in types

    def test_llm_event_carries_usage_and_metrics_aggregate(self):
        """LLM_RESPONSE 携带 token，MetricsAggregator 聚合为 TaskMetrics。"""
        llm = FakeChatLLM(
            responses=[AIMessage(content="hi", response_metadata=_usage(10, 5))]
        )
        agent = _agent(llm)
        list(agent.stream(messages=[{"role": "user", "content": "x"}], session_id="s1"))
        events = agent.event_emitter.store.query()
        llm_ev = [e for e in events if e.event_type == AgentEventType.LLM_RESPONSE]
        assert len(llm_ev) == 1
        assert llm_ev[0].tokens_in == 10
        assert llm_ev[0].tokens_out == 5

        agent_metrics, task_metrics = MetricsAggregator.aggregate(events)
        assert task_metrics.total_tokens_in == 10
        assert task_metrics.total_tokens_out == 5
        assert agent_metrics["main"].llm_call_count == 1

    def test_events_without_otel_have_empty_trace_id(self):
        """未接入 OTel（默认）时事件 trace_id 留空（FR-010）。"""
        llm = FakeChatLLM(
            responses=[AIMessage(content="hi", response_metadata=_usage())]
        )
        agent = _agent(llm)
        list(agent.stream(messages=[{"role": "user", "content": "x"}], session_id="s1"))
        events = agent.event_emitter.store.query()
        assert all(not e.trace_id for e in events)

    def test_tool_call_emits_events(self):
        """工具调用（工具缺失）产生 TOOL_CALL_START 与 TOOL_ERROR。"""
        llm = FakeChatLLM(
            responses=[
                AIMessage(content="", tool_calls=[_tool_call()], response_metadata=_usage(5, 3)),
                AIMessage(content="done", response_metadata=_usage(8, 4)),
            ]
        )
        agent = _agent(llm)
        list(agent.stream(messages=[{"role": "user", "content": "run"}], session_id="s1"))
        events = agent.event_emitter.store.query()
        types = {e.event_type for e in events}
        assert AgentEventType.TOOL_CALL_START in types
        assert AgentEventType.TOOL_ERROR in types  # 工具缺失 → error
