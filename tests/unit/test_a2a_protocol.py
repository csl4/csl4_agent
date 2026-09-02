"""A2A 协议封装层单测（agent/core/a2a/protocol.py）。"""

import pytest

from agent.core.a2a import protocol as p
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
)


class TestMakeTask:
    def test_task_starts_submitted(self) -> None:
        task = p.make_task("task-1", context_id="ctx-1")
        assert task.id == "task-1"
        assert task.context_id == "ctx-1"
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED

    def test_default_context(self) -> None:
        task = p.make_task("task-1")
        assert task.context_id == ""


class TestSetTaskState:
    def test_set_completed_with_message(self) -> None:
        task = p.make_task("task-1")
        p.set_task_state(task, TaskState.TASK_STATE_COMPLETED, "done", timestamp=False)
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert task.status.message.role == Role.ROLE_AGENT
        assert task.status.message.parts[0].text == "done"

    def test_timestamp_set(self) -> None:
        task = p.make_task("task-1")
        p.set_task_state(task, TaskState.TASK_STATE_WORKING)
        assert task.status.HasField("timestamp")
        assert task.status.timestamp.ToSeconds() > 0


class TestStateHelpers:
    def test_terminal_states(self) -> None:
        for state in (
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_REJECTED,
        ):
            assert p.is_terminal(state) is True
        assert p.is_terminal(TaskState.TASK_STATE_WORKING) is False

    def test_failed_states(self) -> None:
        assert p.is_failed(TaskState.TASK_STATE_FAILED) is True
        assert p.is_failed(TaskState.TASK_STATE_CANCELED) is True
        assert p.is_failed(TaskState.TASK_STATE_COMPLETED) is False

    def test_state_name(self) -> None:
        assert p.task_state_name(TaskState.TASK_STATE_COMPLETED) == "TASK_STATE_COMPLETED"


class TestParts:
    def test_text_part(self) -> None:
        part = p.make_text_part("hello")
        assert part.text == "hello"

    def test_data_part(self) -> None:
        part = p.make_data_part({"key": "value", "n": 1})
        assert part.HasField("data")
        assert dict(part.data.struct_value) == {"key": "value", "n": 1.0}


class TestMessage:
    def test_make_message(self) -> None:
        msg = p.make_message(Role.ROLE_AGENT, "hi", task_id="t1")
        assert isinstance(msg, Message)
        assert msg.role == Role.ROLE_AGENT
        assert msg.task_id == "t1"
        assert msg.parts[0].text == "hi"


class TestJsonRoundTrip:
    def test_task_json_round_trip(self) -> None:
        task = p.make_task("task-1", context_id="ctx-1")
        p.set_task_state(task, TaskState.TASK_STATE_COMPLETED, "ok", timestamp=False)
        restored = p.task_from_json(p.task_to_json(task))
        assert isinstance(restored, Task)
        assert restored.id == task.id
        assert restored.status.state == TaskState.TASK_STATE_COMPLETED


class TestAgentCard:
    def test_agent_card_for(self) -> None:
        card = p.agent_card_for("orchestrator", "拆解与调度")
        assert isinstance(card, AgentCard)
        assert card.name == "orchestrator"
        assert card.description == "拆解与调度"
        assert card.version == "1.0.0"
        assert card.capabilities is not None


class TestImports:
    def test_reexports_sdk_types(self) -> None:
        # 复用 SDK 类型，而非自造
        assert p.Task is Task
        assert p.Part is Part
        assert p.TaskState is TaskState
