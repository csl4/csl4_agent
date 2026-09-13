"""事件仓库单元测试（spec SC-001 时间序、spec Edge Case 并发，FR-005 单机）。"""

import threading
import uuid

from GSagent.core.observability.models import AgentEventEnvelope, AgentEventType
from GSagent.core.observability.store import EventStore, MemoryEventStore


def ev(**kw) -> AgentEventEnvelope:
    base = dict(
        session_id="s1",
        task_id="t1",
        agent_id="a1",
        event_type=AgentEventType.LLM_REQUEST,
        timestamp=f"2026-09-10T00:00:{0:02d}.000000+00:00",
    )
    base.update(kw)
    return AgentEventEnvelope(**base)


class TestAppendQuery:
    def test_filter_by_task(self):
        store = MemoryEventStore()
        store.append(ev(task_id="t1"))
        store.append(ev(task_id="t2"))
        assert [e.task_id for e in store.query(task_id="t1")] == ["t1"]

    def test_filter_by_session_agent_type(self):
        store = MemoryEventStore()
        store.append(ev(session_id="s1", agent_id="a1"))
        store.append(ev(session_id="s2", agent_id="a2"))
        assert len(store.query(session_id="s1")) == 1
        assert len(store.query(agent_id="a2")) == 1
        assert len(store.query(event_type=AgentEventType.LLM_REQUEST)) == 2

    def test_time_ascending_order(self):
        """查询按时间升序（SC-001 完整时间序）。"""
        store = MemoryEventStore()
        store.append(ev(timestamp="2026-09-10T00:00:03.000000+00:00"))
        store.append(ev(timestamp="2026-09-10T00:00:01.000000+00:00"))
        store.append(ev(timestamp="2026-09-10T00:00:02.000000+00:00"))
        ts = [e.timestamp for e in store.query(task_id="t1")]
        assert ts == sorted(ts)

    def test_limit(self):
        store = MemoryEventStore()
        for i in range(5):
            store.append(ev(timestamp=f"2026-09-10T00:00:{i:02d}.000000+00:00"))
        assert len(store.query(task_id="t1", limit=3)) == 3

    def test_replay_full_sequence(self):
        """replay 返回单任务完整事件序列（spec US1）。"""
        store = MemoryEventStore()
        for t in ("agent.start", "llm.request", "llm.response", "tool.call.start"):
            store.append(ev(event_type=AgentEventType(t)))
        seq = store.replay("t1")
        assert [e.event_type.value for e in seq] == [
            "agent.start", "llm.request", "llm.response", "tool.call.start",
        ]


class TestSnapshotIsolation:
    def test_query_returns_deep_copy(self):
        """查询返回深拷贝，外部修改不影响仓库。"""
        store = MemoryEventStore()
        store.append(ev(payload={"k": "v"}))
        snap = store.query(task_id="t1")[0]
        snap.payload["k"] = "mutated"
        assert store.query(task_id="t1")[0].payload["k"] == "v"

    def test_orphan_event_graceful(self):
        """孤儿事件（parent_span_id 指向不存在的 span）查询不报错（spec Edge Case）。"""
        store = MemoryEventStore()
        store.append(ev(parent_span_id="missing-span"))
        assert len(store.query(task_id="t1")) == 1


class TestConcurrency:
    def test_concurrent_append_no_loss(self):
        """并发 append 同一任务：原子追加、事件不丢（spec Edge Case 并发写入）。"""
        store = MemoryEventStore()
        n = 50
        barrier = threading.Barrier(4)

        def worker(offset: int) -> None:
            barrier.wait()
            for i in range(n):
                store.append(
                    ev(
                        task_id="t1",
                        timestamp=f"2026-09-10T00:00:{i + offset:03d}.000000+00:00",
                    )
                )

        threads = [threading.Thread(target=worker, args=(i * n,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(store) == 4 * n
        # 查询无丢失，且排序后条数一致
        assert len(store.query(task_id="t1")) == 4 * n

    def test_clear(self):
        store = MemoryEventStore()
        store.append(ev())
        store.clear()
        assert len(store) == 0
        assert store.query(task_id="t1") == []


class TestProtocolConformance:
    def test_memory_store_satisfies_event_store_protocol(self):
        """MemoryEventStore 满足 EventStore 契约（分布式实现可替换，FR-005）。"""
        assert isinstance(MemoryEventStore(), EventStore)
