"""A2A 进程内通信客户端单测（agent/core/a2a/client.py，T010）。"""

import pytest

from agent.core.a2a.client import A2AClientError, InProcessA2AClient
from agent.core.a2a.protocol import Role, Task, TaskState, set_task_state
from agent.core.agents.base_agent import AgentRole, BaseAgent, task_input_text


class EchoAgent(BaseAgent):
    """测试用目标 Agent：把任务文本回显为完成消息；可按需先失败 N 次。"""

    def __init__(self, agent_id: str = "echo", fail_first: int = 0) -> None:
        super().__init__(agent_id, AgentRole.BUSINESS)
        self.calls = 0
        self.fail_first = fail_first

    def run_task(self, task: Task) -> Task:
        self.calls += 1
        if self.calls <= self.fail_first:
            set_task_state(task, TaskState.TASK_STATE_FAILED, "transient-failure")
            return task
        text = task_input_text(task, self.context_messages())
        set_task_state(task, TaskState.TASK_STATE_COMPLETED, f"echo:{text}")
        return task


class TestInProcessClientSend:
    def test_send_task_returns_completed(self) -> None:
        target = EchoAgent()
        client = InProcessA2AClient(target)
        result = client.send_task("hello")
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        assert result.status.message.parts[0].text == "echo:hello"

    def test_custom_task_id(self) -> None:
        target = EchoAgent()
        client = InProcessA2AClient(target)
        result = client.send_task("hello", task_id="my-task-1")
        assert result.id == "my-task-1"

    def test_user_message_written_to_context(self) -> None:
        target = EchoAgent()
        client = InProcessA2AClient(target)
        client.send_task("ping")
        user_msgs = [m for m in target.context if m.role == Role.ROLE_USER]
        assert len(user_msgs) == 1
        assert user_msgs[0].parts[0].text == "ping"

    def test_retries_transient_failure(self) -> None:
        """第一次失败 → tenacity 自动重试并成功（宪法 V：重试走 tenacity）。"""
        target = EchoAgent(fail_first=1)
        client = InProcessA2AClient(target)
        result = client.send_task("retry-me")
        assert target.calls == 2
        assert result.status.state == TaskState.TASK_STATE_COMPLETED

    def test_raises_after_retries_exhausted(self) -> None:
        target = EchoAgent(fail_first=99)
        client = InProcessA2AClient(target)
        with pytest.raises(A2AClientError):
            client.send_task("doomed")
        assert target.calls == 3


class TestInProcessClientStream:
    def test_stream_task_snapshots(self) -> None:
        """stream_task 逐步产出 submitted→working→terminal 的独立快照。"""
        target = EchoAgent()
        client = InProcessA2AClient(target)
        states = [
            TaskState.Name(snap.status.state)
            for snap in client.stream_task("hi")
        ]
        assert states == [
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_COMPLETED",
        ]

    def test_stream_final_carries_result(self) -> None:
        target = EchoAgent()
        client = InProcessA2AClient(target)
        snaps = list(client.stream_task("hi"))
        assert snaps[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert snaps[-1].status.message.parts[0].text == "echo:hi"
