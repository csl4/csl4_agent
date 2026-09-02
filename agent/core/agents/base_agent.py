"""Agent 角色的统一抽象（BaseAgent）。

设计（宪法 V：字段/方法放最通用层级）：
- `AgentRole` 枚举固定四个角色：main / orchestrator / business / subagent。
- `BaseAgent` 是所有角色的基类：持有角色、实例 ID、上下文（A2A Message 列表）、
  父级引用（SubAgent 必填）。
- 协作以 A2A `Task` / `Message` 交换（见 agent/core/a2a/protocol.py）。
- 重试统一走 tenacity（宪法 V：不手写重试循环）。
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from agent.core.a2a.protocol import (
    FAILED_STATES,
    Message,
    Role,
    Task,
    TaskState,
    is_failed,
    is_terminal,
    make_message,
    make_task,
    set_task_state,
)

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Agent 角色。"""

    MAIN = "main"
    ORCHESTRATOR = "orchestrator"
    BUSINESS = "business"
    SUBAGENT = "subagent"


# 默认重试策略：最多 3 次，指数退避（宪法 V：重试走 tenacity）
RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
)


class BaseAgent(ABC):
    """所有 Agent 角色的基类。

    子类只需实现 `run_task(task) -> Task`：接收一个 A2A Task，处理并返回
    状态已更新（completed/failed）的结果 Task。
    """

    def __init__(
        self,
        agent_id: str,
        role: AgentRole,
        name: str = "",
        parent: Optional["BaseAgent"] = None,
        knowledge_text: str = "",
    ) -> None:
        self.agent_id = agent_id
        self.role = role
        self.name = name or f"{role.value}-{agent_id}"
        self.parent = parent
        self.context: List[Message] = []
        # US3 FR-007：环境/知识上下文，注入该角色的 LLM 提示词（无 LLM 的角色忽略）。
        self.knowledge_text = knowledge_text

    # ---- 上下文管理 ----
    def add_context(self, message: Message) -> None:
        """把一条 A2A Message 追加到本 Agent 的上下文窗口。"""
        self.context.append(message)

    def context_messages(self) -> List[Message]:
        """当前上下文消息快照。"""
        return list(self.context)

    def context_text(self) -> str:
        """上下文文本视图（供 LLM 提示词使用）。"""
        lines = []
        for msg in self.context:
            for part in msg.parts:
                if part.HasField("text"):
                    lines.append(f"[{msg.role}] {part.text}")
        return "\n".join(lines)

    # ---- 协作：发送 ----
    @RETRY_DECORATOR
    def send(self, to: "BaseAgent", text: str, task: Optional[Task] = None) -> Task:
        """发送一条 A2A Message 给目标 Agent，返回携带的 Task。

        若未显式传 task，则新建一个 SUBMITTED Task 作为本次协作的工作单元。
        """
        out_task = task or make_task(f"{self.agent_id}->{to.agent_id}")
        msg = make_message(
            Role.ROLE_AGENT, text, task_id=out_task.id, context_id=out_task.context_id
        )
        self.add_context(msg)
        to.add_context(msg)
        logger.debug(
            "agent %s -> %s: task=%s role=%s", self.name, to.name, out_task.id, self.role.value
        )
        return out_task

    # ---- 协作：处理 ----
    @abstractmethod
    def run_task(self, task: Task) -> Task:
        """处理一个 A2A Task，返回状态已更新（completed/failed）的 Task。

        实现约定：
        - 成功 → `set_task_state(task, TASK_STATE_COMPLETED, ...)`
        - 失败 → `set_task_state(task, TASK_STATE_FAILED, ...)`，调用方可用 tenacity 重试
        """

    def _run_task_with_retry(self, task: Task) -> Task:
        """带重试地执行 run_task：失败态自动重试，终止态直接返回。"""
        if is_terminal(task.status.state):
            return task

        @RETRY_DECORATOR
        def _run() -> Task:
            result = self.run_task(task)
            if is_failed(result.status.state):
                raise RuntimeError(f"Task {result.id} failed: {task_state_text(result)}")
            return result

        return _run()

    # ---- 帮助方法 ----
    def task_card(self) -> str:
        """给 LLM/CLI 看的 Agent 自描述（对齐 A2A AgentCard 的语义字段）。"""
        return (
            f"name={self.name} | role={self.role.value} | id={self.agent_id} | "
            f"parent={self.parent.name if self.parent else '-'}"
        )


def task_state_text(task: Task) -> str:
    """Task 状态的可读文本（含消息，若有）。"""
    state = TaskState.Name(task.status.state)
    text = ""
    if task.status.HasField("message"):
        for part in task.status.message.parts:
            if part.HasField("text"):
                text = part.text
                break
    return f"{state}: {text}" if text else state


def task_result_text(task: Task) -> str:
    """Task 的完成/失败消息文本（运行结果，区别于输入文本）。"""
    if not task.status.HasField("message"):
        return ""
    parts = [p.text for p in task.status.message.parts if p.HasField("text")]
    return "\n".join(parts)


def task_input_text(task: Task, messages: Optional[List[Message]] = None) -> str:
    """提取 Task 的【输入】任务文本。

    优先取上下文里与 task 同 id 的最近 ROLE_USER 消息（客户端写入），
    其次回退到 task.status.message 的文本 —— 保证重试/多轮时取到的始终是
    原始输入，而非上一次运行的输出。
    """
    if messages:
        for msg in reversed(messages):
            if msg.task_id == task.id and msg.role == Role.ROLE_USER:
                parts = [p.text for p in msg.parts if p.HasField("text")]
                if parts:
                    return "\n".join(parts)
    if task.status.HasField("message"):
        parts = [p.text for p in task.status.message.parts if p.HasField("text")]
        if parts:
            return "\n".join(parts)
    return ""


def run_task_safe(agent: BaseAgent, task: Task) -> Task:
    """执行 agent.run_task 并保证返回终止态 Task（失败时置 FAILED 不抛异常）。

    用于编排层批量调度：单个 SubAgent 失败不中断整体，由调用方决定重试/归并。
    """
    try:
        result = agent.run_task(task)
        if not is_terminal(result.status.state):
            set_task_state(
                result, TaskState.TASK_STATE_FAILED, "run_task returned non-terminal state"
            )
        return result
    except Exception as exc:  # noqa: BLE001 - 编排层兜底，不向上抛
        logger.exception("agent %s run_task raised: %s", agent.name, exc)
        set_task_state(task, TaskState.TASK_STATE_FAILED, f"run_task raised: {exc}")
        return task


# 供 retry 判定使用的别名（保持 FAILED_STATES 单一来源可被测试引用）
__all__ = [
    "AgentRole",
    "BaseAgent",
    "FAILED_STATES",
    "run_task_safe",
    "task_input_text",
    "task_result_text",
    "task_state_text",
]
