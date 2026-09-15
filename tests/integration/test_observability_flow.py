"""对象模型端到端集成测试（spec US1/US2 验收场景，quickstart V1-V5）。"""

from GSagent.core.observability import (
    EventEmitter,
    MemoryEventStore,
    MetricsAggregator,
    SessionStatus,
    TaskStatus,
)
from GSagent.core.observability.models import AgentEventEnvelope, AgentEventType, AgentStatus


def make_event(
    session_id: str,
    task_id: str,
    agent_id: str,
    event_type: AgentEventType,
    timestamp: str,
    **kw,
) -> AgentEventEnvelope:
    return AgentEventEnvelope(
        session_id=session_id,
        task_id=task_id,
        agent_id=agent_id,
        event_type=event_type,
        timestamp=timestamp,
        **kw,
    )


def build_us1_fixture() -> MemoryEventStore:
    """构造「会话 s1 → 任务 t1 → 2 Agent → 事件流」，混入另一任务事件制造交错。"""
    store = MemoryEventStore()
    events = [
        # 任务 t1：agent a1（orchestrator）为主链
        make_event("s1", "t1", "a1", AgentEventType.AGENT_START, "2026-09-10T00:00:01+00:00"),
        make_event("s1", "t1", "a1", AgentEventType.LLM_REQUEST, "2026-09-10T00:00:02+00:00"),
        make_event("s1", "t1", "a1", AgentEventType.LLM_RESPONSE, "2026-09-10T00:00:03+00:00"),
        # 任务 t2（干扰）：交错插入
        make_event("s1", "t2", "a2", AgentEventType.AGENT_START, "2026-09-10T00:00:04+00:00"),
        # 回到 t1：agent a1 调工具
        make_event("s1", "t1", "a1", AgentEventType.TOOL_CALL_START, "2026-09-10T00:00:05+00:00", parent_span_id="span-x"),
        make_event("s1", "t1", "a1", AgentEventType.TOOL_CALL_END, "2026-09-10T00:00:06+00:00", parent_span_id="span-x"),
        # 孤儿事件（parent_span_id 指向不存在 span）
        make_event("s1", "t1", "a1", AgentEventType.STATE_UPDATE, "2026-09-10T00:00:07+00:00", parent_span_id="missing"),
        make_event("s1", "t1", "a1", AgentEventType.AGENT_END, "2026-09-10T00:00:08+00:00"),
    ]
    for e in events:
        store.append(e)
    return store


class TestUserStory1LinkDrilldown:
    """US1 任务级链路钻取与问题定位（spec US1 验收场景 1/2/3）。"""

    def test_replay_full_time_ordered_sequence(self):
        """按 task_id 查询返回完整、时间升序的事件序列（SC-001）。"""
        store = build_us1_fixture()
        seq = store.replay("t1")
        types = [e.event_type.value for e in seq]
        assert types == [
            "agent.start", "llm.request", "llm.response",
            "tool.call.start", "tool.call.end", "state.update", "agent.end",
        ]
        timestamps = [e.timestamp for e in seq]
        assert timestamps == sorted(timestamps)

    def test_task_filter_no_cross_talk(self):
        """混入另一任务事件后按 task_id 过滤不串扰。"""
        store = build_us1_fixture()
        assert len(store.replay("t1")) == 7
        assert len(store.replay("t2")) == 1

    def test_span_parent_traceback_and_orphan_graceful(self):
        """沿 span 父子回溯 + 孤儿事件降级不报错（spec Edge Case）。"""
        store = build_us1_fixture()
        tool_events = store.query(task_id="t1", event_type=AgentEventType.TOOL_CALL_START)
        assert len(tool_events) == 1
        assert tool_events[0].parent_span_id == "span-x"
        # 孤儿事件正常返回、不抛异常
        orphan = [e for e in store.replay("t1") if e.parent_span_id == "missing"]
        assert len(orphan) == 1

    def test_session_multiple_tasks_view(self):
        """会话下多任务视图：按 session_id 查询返回全部任务事件（US1 验收场景 3）。"""
        store = build_us1_fixture()
        all_events = store.query(session_id="s1")
        task_ids = {e.task_id for e in all_events}
        assert task_ids == {"t1", "t2"}
        assert len(all_events) == 8

    def test_full_observability_stack(self):
        """四层对象 → 事件 → 指标 → 查询全链路（quickstart V1/V2 端到端）。"""
        store = build_us1_fixture()
        # 会话/任务/Agent 快照
        task = TaskStatus(task_id="t1", session_id="s1", trace_id="tr-1")
        task.agent_status_list.append(
            AgentStatus(agent_id="a1", task_id="t1", name="orchestrator", agent_type="orchestrator")
        )
        session = SessionStatus(session_id="s1", task_list=[task])
        assert session.task_list[0].trace_id == "tr-1"
        # 指标聚合与事件流一致
        agents, task_metrics = MetricsAggregator.aggregate(store.replay("t1"))
        assert agents["a1"].tool_call_count == 1
        assert agents["a1"].llm_call_count == 1
        assert task_metrics.total_error_count == 0


class TestUserStory2SessionAggregation:
    """US2 会话维度跨任务聚合（spec US2 验收场景 1/2）。"""

    def test_cross_task_agent_accumulation(self):
        store = MemoryEventStore()
        store.append(make_event("s1", "t1", "a1", AgentEventType.LLM_REQUEST, "2026-09-10T00:00:01+00:00", tokens_in=10))
        store.append(make_event("s1", "t1", "a1", AgentEventType.LLM_REQUEST, "2026-09-10T00:00:02+00:00", tokens_in=10))
        store.append(make_event("s1", "t2", "a2", AgentEventType.LLM_REQUEST, "2026-09-10T00:00:03+00:00", tokens_in=20))
        agents, _ = MetricsAggregator.aggregate_session(store.query(session_id="s1"))
        assert agents["a1"].llm_call_count == 2
        assert agents["a1"].total_tokens_in == 20
        assert agents["a2"].total_tokens_in == 20

    def test_metrics_source_is_event_stream_only(self):
        """指标一致率 100%：聚合结果与事件流计数一致（SC-003）。"""
        store = build_us1_fixture()
        agents, _ = MetricsAggregator.aggregate_session(store.query(session_id="s1"))
        all_events = store.query(session_id="s1")
        expected_llm = sum(1 for e in all_events if e.event_type == AgentEventType.LLM_REQUEST)
        assert agents["a1"].llm_call_count == expected_llm


class TestEventEmitter:
    def test_emitter_writes_to_store(self):
        """EventEmitter 发射事件入仓库（R-07 接口预留）。"""
        store = MemoryEventStore()
        emitter = EventEmitter(store)
        emitter.emit(
            make_event("s1", "t1", "a1", AgentEventType.APPROVAL_DECISION, "2026-09-10T00:00:01+00:00")
        )
        assert emitter.store is store
        assert len(store.replay("t1")) == 1
        assert store.replay("t1")[0].event_type == AgentEventType.APPROVAL_DECISION
