"""指标聚合单元测试（spec FR-003/SC-003：仅事件流聚合、无手工写入口）。"""

import pytest

from GSagent.core.observability.metrics import MetricsAggregator
from GSagent.core.observability.models import AgentEventEnvelope, AgentEventType, AgentMetrics


def ev(**kw) -> AgentEventEnvelope:
    base = dict(
        session_id="s1",
        task_id="t1",
        agent_id="a1",
        event_type=AgentEventType.LLM_REQUEST,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.01,
        timestamp="2026-09-10T00:00:00.000000+00:00",
    )
    base.update(kw)
    return AgentEventEnvelope(**base)


class TestAgentMetrics:
    def test_counts(self):
        events = [
            ev(event_type=AgentEventType.LLM_REQUEST),
            ev(event_type=AgentEventType.LLM_REQUEST),
            ev(event_type=AgentEventType.LLM_ERROR),
            ev(event_type=AgentEventType.TOOL_CALL_START),
        ]
        agents, _ = MetricsAggregator.aggregate(events)
        m = agents["a1"]
        assert m.llm_call_count == 2
        assert m.tool_call_count == 1
        assert m.error_count == 1  # LLM_ERROR
        assert isinstance(m, AgentMetrics)

    def test_tokens_cost_accumulate(self):
        events = [ev(tokens_in=10, tokens_out=5, cost_usd=0.01) for _ in range(3)]
        agents, _ = MetricsAggregator.aggregate(events)
        m = agents["a1"]
        assert m.total_tokens_in == 30
        assert m.total_tokens_out == 15
        assert round(m.total_cost_usd, 4) == 0.03

    def test_last_event_timestamp(self):
        events = [
            ev(timestamp="2026-09-10T00:00:01.000000+00:00"),
            ev(timestamp="2026-09-10T00:00:02.000000+00:00"),
        ]
        agents, _ = MetricsAggregator.aggregate(events)
        assert agents["a1"].last_event_timestamp == "2026-09-10T00:00:02.000000+00:00"

    def test_empty_agent_id_skipped(self):
        """无 agent_id 事件不形成 Agent 指标，但计入 Task 汇总。"""
        events = [ev(agent_id="")]
        agents, task = MetricsAggregator.aggregate(events)
        assert agents == {}
        assert task.total_tokens_in == 10


class TestTaskMetrics:
    def test_task_aggregation_includes_all_events(self):
        events = [
            ev(agent_id="a1", tokens_in=10, tokens_out=5, cost_usd=0.01),
            ev(agent_id="a2", tokens_in=20, tokens_out=10, cost_usd=0.02),
            ev(agent_id="", tokens_in=30, tokens_out=0, cost_usd=0.03),  # 无 agent 事件也计入
        ]
        _, task = MetricsAggregator.aggregate(events)
        assert task.total_tokens_in == 60
        assert task.total_tokens_out == 15
        assert round(task.total_cost_usd, 4) == 0.06

    def test_task_error_count(self):
        events = [
            ev(event_type=AgentEventType.LLM_ERROR),
            ev(event_type=AgentEventType.TOOL_ERROR),
            ev(event_type=AgentEventType.AGENT_END),
        ]
        _, task = MetricsAggregator.aggregate(events)
        assert task.total_error_count == 2

    def test_empty_stream(self):
        agents, task = MetricsAggregator.aggregate([])
        assert agents == {}
        assert task.total_tokens_in == 0
        assert task.total_error_count == 0


class TestSessionAggregation:
    def test_aggregate_session_cross_task(self):
        """会话级跨任务聚合（spec US2）：同 session 两任务、各自 Agent 累计。"""
        events = [
            # 任务 t1：agent a1 2 次 LLM
            ev(session_id="s1", task_id="t1", agent_id="a1", event_type=AgentEventType.LLM_REQUEST, tokens_in=10),
            ev(session_id="s1", task_id="t1", agent_id="a1", event_type=AgentEventType.LLM_REQUEST, tokens_in=10),
            # 任务 t2：agent a2 1 次 LLM
            ev(session_id="s1", task_id="t2", agent_id="a2", event_type=AgentEventType.LLM_REQUEST, tokens_in=20),
        ]
        agents, _ = MetricsAggregator.aggregate_session(events)
        assert agents["a1"].llm_call_count == 2
        assert agents["a1"].total_tokens_in == 20
        assert agents["a2"].llm_call_count == 1
        assert agents["a2"].total_tokens_in == 20


class TestImmutability:
    def test_no_setter_path(self):
        """指标 frozen：任何赋值路径都被拒绝（SC-003 无手工写入口）。"""
        agents, _ = MetricsAggregator.aggregate([ev()])
        with pytest.raises(Exception):
            agents["a1"].llm_call_count = 99
        _, task = MetricsAggregator.aggregate([ev()])
        with pytest.raises(Exception):
            task.total_tokens_in = 99
