"""业务 ↔ 协议映射单元测试（spec FR-008，contracts/mapping.md）。"""

import pytest
from a2a.types import Task, TaskState, TaskStatus as ProtocolTaskStatus

from GSagent.core.observability.mapping import (
    business_state_to_protocol,
    from_protocol_task,
    protocol_state_to_business,
    to_protocol_task,
)
from GSagent.core.observability.models import TaskStatus


def make_business_task(state: str = "completed", trace_id: str = "tr-1", payload: dict | None = None) -> TaskStatus:
    return TaskStatus(
        task_id="t1", session_id="s1", trace_id=trace_id, state=state, payload=payload or {}
    )


class TestStateMapping:
    def test_business_to_protocol_roundtrip_terminal(self):
        """四种终止态双向严格一致（FR-008）。"""
        for business in ("completed", "failed", "canceled", "rejected"):
            proto_state = business_state_to_protocol(business)
            back = protocol_state_to_business(proto_state)
            assert back == business, f"{business} → {proto_state} → {back}"

    def test_business_to_protocol_full(self):
        """全部业务状态可映射到协议。"""
        for business in ("submitted", "working", "input_required", "completed", "failed", "canceled", "rejected"):
            business_state_to_protocol(business)  # 不抛异常

    def test_unknown_business_state_raises(self):
        """未知业务状态 → ValueError（不静默，contracts/mapping.md）。"""
        with pytest.raises(ValueError):
            business_state_to_protocol("bogus")

    def test_auth_required_maps_to_input_required(self):
        """AUTH_REQUIRED → input_required（spec FR-008 特殊态）。"""
        assert protocol_state_to_business(TaskState.TASK_STATE_AUTH_REQUIRED) == "input_required"

    def test_unspecified_maps_to_working(self):
        """UNSPECIFIED → working 兜底。"""
        assert protocol_state_to_business(TaskState.TASK_STATE_UNSPECIFIED) == "working"


class TestTaskMapping:
    def test_business_to_protocol_fields(self):
        """业务 → 协议：id/context_id/state 正确（contracts §2）。"""
        bt = make_business_task()
        proto = to_protocol_task(bt, business_state_to_protocol("completed"))
        assert proto.id == "t1"
        assert proto.context_id == "s1"
        assert proto.status.state == TaskState.TASK_STATE_COMPLETED

    def test_trace_id_and_payload_via_metadata(self):
        """trace_id/payload 存协议 metadata，往返不丢（contracts §4 不变量）。"""
        bt = make_business_task(payload={"k": "v"})
        proto = to_protocol_task(bt, business_state_to_protocol("completed"))
        m = dict(proto.metadata)
        assert m["trace_id"] == "tr-1"
        assert dict(m["payload"]) == {"k": "v"}

    def test_protocol_to_business_roundtrip(self):
        """协议 → 业务：id/context_id/state/trace_id 往返一致。"""
        proto = to_protocol_task(make_business_task(), business_state_to_protocol("completed"))
        bt = from_protocol_task(proto)
        assert bt.task_id == "t1"
        assert bt.session_id == "s1"
        assert bt.trace_id == "tr-1"
        assert bt.state == "completed"

    def test_missing_trace_id_fallback(self):
        """协议 metadata 无 trace_id → 回退空串不报错。"""
        proto = Task(
            id="t2",
            context_id="s2",
            status=ProtocolTaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        bt = from_protocol_task(proto)
        assert bt.trace_id == ""
        assert bt.state == "working"

    def test_auth_required_preserves_protocol_state(self):
        """AUTH_REQUIRED → input_required 且 payload 保留原文（信息不丢）。"""
        proto = Task(
            id="t3",
            context_id="s3",
            status=ProtocolTaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED),
        )
        bt = from_protocol_task(proto)
        assert bt.state == "input_required"
        assert bt.payload.get("protocol_state") == "TASK_STATE_AUTH_REQUIRED"

    def test_roundtrip_idempotent(self):
        """to→from 往返 id/context_id/state 不变（契约不变量）。"""
        for business in ("completed", "failed", "canceled", "rejected", "working"):
            bt = make_business_task(state=business)
            proto = to_protocol_task(bt, business_state_to_protocol(business))
            back = from_protocol_task(proto)
            assert back.task_id == bt.task_id
            assert back.session_id == bt.session_id
            assert back.state == business
