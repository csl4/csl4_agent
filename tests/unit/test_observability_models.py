"""对象模型单元测试（spec SC-001/SC-004，宪法 IV 镜像 GSagent/core/observability）。"""

import pytest
from pydantic import ValidationError

from GSagent.core.observability.models import (
    AgentEventEnvelope,
    AgentEventType,
    AgentMetrics,
    AgentStatus,
    LogLevel,
    SessionStatus,
    TASK_STATES,
    TaskMetrics,
    TaskStatus,
    payload_redacted,
)


def make_session() -> SessionStatus:
    task = TaskStatus(task_id="t1", session_id="s1", trace_id="tr-1")
    agent = AgentStatus(agent_id="a1", task_id="t1", name="orchestrator", agent_type="orchestrator")
    task.agent_status_list.append(agent)
    return SessionStatus(session_id="s1", task_list=[task])


class TestConstruction:
    def test_required_fields_validated(self):
        """必填字段缺失 → ValidationError（FR-001 实体校验）。"""
        with pytest.raises(ValidationError):
            TaskStatus()  # task_id / trace_id 必填
        with pytest.raises(ValidationError):
            AgentStatus()  # agent_id / task_id / name / agent_type 必填

    def test_defaults_backward_compatible(self):
        """字段带默认值：只传必填即可构造（向后兼容）。"""
        t = TaskStatus(task_id="t", trace_id="tr")
        assert t.state == "submitted"
        assert t.session_id == ""
        assert t.agent_status_list == []
        assert isinstance(t.aggregated_metrics, TaskMetrics)

    def test_task_state_valid_set(self):
        """TaskStatus.state 属于合法状态集（data-model 状态机）。"""
        for s in TASK_STATES:
            assert TaskStatus(task_id="t", trace_id="tr", state=s).state == s

    def test_empty_session_and_task(self):
        """空会话 / 空任务可正常表示（spec Edge Case）。"""
        s = SessionStatus(session_id="s")
        assert s.task_list == []
        t = TaskStatus(task_id="t", trace_id="tr")
        assert t.agent_status_list == []


class TestSerialization:
    def test_json_roundtrip(self):
        """JSON 往返一致（contracts/observability.md §3，SC-001）。"""
        s = make_session()
        data = s.model_dump(mode="json")
        s2 = SessionStatus.model_validate(data)
        assert s2.session_id == s.session_id
        assert s2.task_list[0].task_id == "t1"
        assert s2.task_list[0].trace_id == "tr-1"
        assert s2.task_list[0].agent_status_list[0].agent_id == "a1"

    def test_nested_serialization_shapes(self):
        """序列化结构：task_list / agent_status_list / aggregated_metrics 齐全。"""
        data = make_session().model_dump(mode="json")
        assert data["session_id"] == "s1"
        task = data["task_list"][0]
        assert set(task.keys()) >= {"task_id", "session_id", "trace_id", "state", "agent_status_list", "aggregated_metrics"}


class TestPrivacy:
    def test_capture_full_content_default_off(self):
        """隐私默认关（spec FR-004）：capture_full_content=False。"""
        ev = AgentEventEnvelope(payload={"prompt": "secret-content"})
        assert ev.capture_full_content is False

    def test_payload_redacted_strips_sensitive_keys(self):
        """脱敏辅助剔除敏感键（SC-004 泄漏为 0，对齐审计脱敏语义）。"""
        ev = AgentEventEnvelope(
            payload={"prompt": "ok", "api_key": "sk-123", "nested": {"password": "p", "normal": 1}}
        )
        red = ev.redacted_payload()
        assert "api_key" not in red
        assert "password" not in red["nested"]
        assert red["prompt"] == "ok"
        assert red["nested"]["normal"] == 1
        # 原对象不受影响
        assert "api_key" in ev.payload

    def test_redacted_returns_copy(self):
        """脱敏返回新 dict，不改原 payload。"""
        p = {"secret": "x"}
        out = payload_redacted(p)
        assert out != p
        assert "secret" in p


class TestEventTypes:
    def test_nine_groups_present(self):
        """九类事件齐全（spec FR-007）。"""
        values = {e.value for e in AgentEventType}
        # 新增三类：审批 / A2A 消息 / 规划
        assert "approval.required" in values
        assert "approval.decision" in values
        assert "a2a.message.sent" in values
        assert "a2a.message.received" in values
        assert "plan.generated" in values
        assert "plan.task.started" in values
        assert "plan.task.done" in values
        # 既有六类代表性成员
        for v in ("agent.start", "llm.request", "tool.call.start", "reasoning", "state.update", "error.recovery"):
            assert v in values


class TestFrozenMetrics:
    def test_agent_metrics_immutable(self):
        """指标无 setter（宪法：仅事件流聚合生成，spec FR-003/SC-003）。"""
        m = AgentMetrics(llm_call_count=2)
        with pytest.raises(Exception):
            m.llm_call_count = 3  # frozen → 赋值报错

    def test_task_metrics_immutable(self):
        m = TaskMetrics(total_tokens_in=1)
        with pytest.raises(Exception):
            m.total_tokens_in = 2
