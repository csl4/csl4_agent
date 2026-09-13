"""Agent 角色的统一抽象（BaseAgent）。

设计（宪法 V：字段/方法放最通用层级）：
- `AgentRole` 枚举固定四个角色：main / orchestrator / business / subagent。
- `BaseAgent` 是所有角色的基类：持有角色、实例 ID、上下文（OpenAI 风格消息
  字典 `[{"role": ..., "content": ...}]`，可直接喂 LLM）、父级引用（SubAgent 必填）。
- 协作以 A2A `Task` 交换（见 GSagent/core/a2a/protocol.py）；Task 输入文本另存
  `task_inputs` 注册表（task_id → 原始文本），保持消息字典纯净（不带 task 元数据）。
- 重试统一走 tenacity（宪法 V：不手写重试循环）。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from GSagent.core.a2a.protocol import (
    FAILED_STATES,
    Task,
    TaskState,
    fail_task,
    is_failed,
    is_terminal,
    make_task,
    set_task_state,
    task_message_text,
)

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Agent 角色。"""

    MAIN = "main"
    ORCHESTRATOR = "orchestrator"
    BUSINESS = "business"
    SUBAGENT = "subagent"


@dataclass
class SubtaskResult:
    """一次子任务执行的类型化记录（业务层对象化，替代裸 dict）。

    由 Orchestrator._dispatch 生成，供 last_records / merge_results /
    _attach_subtasks / MainAgent._subtask_events 统一以字段访问。
    """

    index: int
    kind: str
    text: str
    worker: str
    state: str
    result: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转为 dict（供 data Part / 流事件序列化）。"""
        return {
            "index": self.index,
            "kind": self.kind,
            "text": self.text,
            "worker": self.worker,
            "state": self.state,
            "result": self.result,
        }


def subtask_dicts(records: List[SubtaskResult]) -> List[Dict[str, Any]]:
    """把 SubtaskResult 记录序列化为事件/data Part 用 dict（不含 result）。

    供 Orchestrator._attach_subtasks 与 MainAgent._subtask_events 统一使用，
    以 to_dict() 为单一序列化来源（B1 去重，避免两处手写同构 dict）。
    """
    return [
        {k: v for k, v in r.to_dict().items() if k != "result"}
        for r in records
    ]


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
        # 内部消息列表：OpenAI 风格结构化消息字典 `{"role": ..., "content": ...}`，
        # 可直接喂 LLM（无需在消息里混入 task 元数据）。
        self.context: List[Dict[str, Any]] = []
        # task_id → 原始任务文本（task_input_text 优先读取，见下）。
        self.task_inputs: Dict[str, str] = {}
        # US3 FR-007：环境/知识上下文，注入该角色的 LLM 提示词（无 LLM 的角色忽略）。
        self.knowledge_text = knowledge_text

    # ---- 上下文管理 ----
    def add_context(self, message: Dict[str, Any]) -> None:
        """把一条 OpenAI 风格消息字典追加到本 Agent 的上下文窗口。"""
        self.context.append(message)

    def record_task_input(self, task_id: str, text: str) -> None:
        """记录一次 Task 的原始输入文本（供 task_input_text 读取）。"""
        self.task_inputs[task_id] = text

    def context_messages(self) -> List[Dict[str, Any]]:
        """当前上下文消息快照。"""
        return list(self.context)

    def context_text(self) -> str:
        """上下文文本视图（供 LLM 提示词使用）。"""
        lines = []
        for msg in self.context:
            text = message_text(msg.get("content") if isinstance(msg, dict) else "")
            if text:
                lines.append(f"[{msg.get('role', '?')}] {text}")
        return "\n".join(lines)

    # ---- 协作：发送 ----
    @RETRY_DECORATOR
    def send(self, to: "BaseAgent", text: str, task: Optional[Task] = None) -> Task:
        """发送一条消息给目标 Agent，返回携带的 Task。

        若未显式传 task，则新建一个 SUBMITTED Task 作为本次协作的工作单元。
        消息以 OpenAI 风格字典 `{"role": "assistant", "content": text}` 写入双方上下文。
        """
        out_task = task or make_task(f"{self.agent_id}->{to.agent_id}")
        msg: Dict[str, Any] = {"role": "assistant", "content": text}
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
    """Task 的完成/失败消息文本（运行结果，区别于输入文本）。

    委托 a2a.protocol.task_message_text（单一提取来源，B2 去重）。
    """
    return task_message_text(task)


def message_text(content: Any) -> str:
    """从 OpenAI 风格消息 content 提取纯文本（兼容 str 与多模态 list）。

    供 context_text / MainAgent._last_user_text 等统一使用（B3 去重）。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(c.get("text", ""))
            for c in content
            if isinstance(c, dict) and c.get("text")
        ).strip()
    return ""


def task_input_text(task: Task, agent: Optional[BaseAgent] = None) -> str:
    """提取 Task 的【输入】任务文本。

    优先取 agent.task_inputs 里与 task 同 id 的原始输入（客户端写入时记录），
    其次回退到 task.status.message 的文本 —— 保证重试/多轮时取到的始终是
    原始输入，而非上一次运行的输出。
    """
    if agent is not None:
        text = agent.task_inputs.get(task.id)
        if text:
            return text
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
            fail_task(result, "run_task returned non-terminal state")
        return result
    except Exception as exc:  # noqa: BLE001 - 编排层兜底，不向上抛
        logger.exception("agent %s run_task raised: %s", agent.name, exc)
        return fail_task(task, f"run_task raised: {exc}")


# 供 retry 判定使用的别名（保持 FAILED_STATES 单一来源可被测试引用）
__all__ = [
    "AgentRole",
    "BaseAgent",
    "FAILED_STATES",
    "SubtaskResult",
    "message_text",
    "run_task_safe",
    "subtask_dicts",
    "task_input_text",
    "task_result_text",
    "task_state_text",
]
