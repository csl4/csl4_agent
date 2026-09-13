"""A2A 通信客户端（transport 无关抽象 + 进程内实现）。

对齐 contracts/a2a.md：调用方向目标 Agent 发送 / 流式 Task。
当前 transport 为 in-process —— 直接驱动目标 Agent 的 `run_task`；
未来接入真实 HTTP A2A 端点时，仅需新增 `send_task` / `stream_task`
的实现，调用方（编排层）无需改动（宪法 V：面向抽象编程）。

设计要点：
- 本模块**不依赖 agents 层**（避免 a2a ↔ agents 循环导入）：目标只做
  鸭子类型（需 `run_task(task) -> Task`、`add_context(dict)`、
  `record_task_input(task_id, text)`、`agent_id` / `name` 属性），
  即 BaseAgent 的实现天然满足。
- `send_task` 失败按 tenacity 策略自动重试（宪法 V：重试走 tenacity）。
- 任务文本以 OpenAI 风格 user 消息字典写入目标上下文，原始输入另存
  `task_inputs` 注册表（`task_input_text` 优先读取）；执行异常由本客户端
  兜底标记 FAILED，不向调用方抛原始异常。
"""

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Generator, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from GSagent.core.a2a.protocol import (
    Task,
    TaskState,
    copy_task,
    fail_task,
    is_failed,
    is_terminal,
    make_task,
    set_task_state,
    task_message_text,
)

logger = logging.getLogger(__name__)


class A2AClientError(RuntimeError):
    """A2A 通信/任务执行失败（重试耗尽后抛出）。"""


class A2AClient(ABC):
    """A2A 客户端抽象：向目标 Agent 发送 / 流式 Task（transport 无关）。"""

    @abstractmethod
    def send_task(self, task_text: str, task_id: str = "") -> Task:
        """发送任务文本并返回终止态（completed/failed）Task。

        实现约定：失败自动重试，重试耗尽后抛 A2AClientError。
        """

    @abstractmethod
    def stream_task(
        self, task_text: str, task_id: str = ""
    ) -> Generator[Task, None, None]:
        """发送任务并逐步产出 Task 状态快照（submitted→working→终止）。

        每次 yield 的都是深拷贝快照，调用方可安全持有而不会随任务推进被改写。
        """


class InProcessA2AClient(A2AClient):
    """进程内 transport：直接驱动目标 Agent 的 `run_task`。

    目标做鸭子类型（BaseAgent 满足）：`run_task(task) -> Task`、
    `add_context(dict)`、`record_task_input(task_id, text)`、
    `agent_id` / `name` 属性。
    """

    def __init__(self, target: Any):
        self.target = target

    def _new_task(self, task_text: str, task_id: str = "") -> Task:
        """新建 SUBMITTED Task，并把任务文本写入目标上下文（user 消息 + 输入注册表）。"""
        tid = task_id or f"task-{self.target.agent_id}-{uuid.uuid4().hex[:8]}"
        task = make_task(tid)
        self.target.add_context({"role": "user", "content": task_text})
        record = getattr(self.target, "record_task_input", None)
        if record is not None:
            record(tid, task_text)
        return task

    def send_task(self, task_text: str, task_id: str = "") -> Task:
        task = self._new_task(task_text, task_id)
        return self._send_with_retry(task)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
        reraise=True,  # 重试耗尽后抛原始 A2AClientError，而非 tenacity.RetryError
    )
    def _send_with_retry(self, task: Task) -> Task:
        # 每次尝试前把 Task 重置回 SUBMITTED（幂等可重放；task_input_text
        # 优先读 task_inputs 注册表里的原始输入，因此重试不会误读上一次的输出）。
        set_task_state(
            task,
            TaskState.TASK_STATE_SUBMITTED,
            message_text=None,
            timestamp=False,
        )
        try:
            result = self.target.run_task(task)
        except Exception as exc:  # noqa: BLE001 - 兜底标记 FAILED，不向上抛原始异常
            logger.exception("target.run_task raised: %s", exc)
            result = fail_task(task, f"run_task raised: {exc}")
        if not is_terminal(result.status.state):
            result = fail_task(result, "run_task 返回了非终止状态")
        if is_failed(result.status.state):
            raise A2AClientError(
                f"Task {result.id} failed: {task_message_text(result) or result.status.state}"
            )
        return result

    def stream_task(
        self, task_text: str, task_id: str = ""
    ) -> Generator[Task, None, None]:
        task = self._new_task(task_text, task_id)
        yield copy_task(task)  # submitted
        set_task_state(task, TaskState.TASK_STATE_WORKING, "processing")
        yield copy_task(task)  # working
        result = self._send_with_retry(task)
        yield copy_task(result)  # terminal
