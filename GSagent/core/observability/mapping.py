"""业务模型 ↔ a2a-sdk 协议模型双向映射（spec FR-008，contracts/mapping.md）。

复用 ``GSagent/core/a2a/protocol.py`` re-export 的 ``a2a.types`` 类型（宪法 V：
不自造协议轮子）。映射规则：
- ``SessionStatus.session_id ↔ Task.context_id``、``TaskStatus.task_id ↔ Task.id``
- 终止态双向严格一致（completed/failed/canceled/rejected）
- ``AUTH_REQUIRED`` / 未知协议态 → 业务 ``input_required``/``working``，
  原状态保留在 ``payload["protocol_state"]``（信息不丢）
- 业务 ``trace_id`` / ``payload`` 存协议 ``Task.metadata``（Struct，不污染协议顶层 schema）
"""

from typing import Any, Dict

from a2a.types import Task, TaskState, TaskStatus as ProtocolTaskStatus

from GSagent.core.observability.models import TaskStatus

# 业务状态 → 协议 TaskState（严格双向）
_BUSINESS_TO_PROTOCOL: Dict[str, TaskState] = {
    "submitted": TaskState.TASK_STATE_SUBMITTED,
    "working": TaskState.TASK_STATE_WORKING,
    "input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
    "completed": TaskState.TASK_STATE_COMPLETED,
    "failed": TaskState.TASK_STATE_FAILED,
    "canceled": TaskState.TASK_STATE_CANCELED,
    "rejected": TaskState.TASK_STATE_REJECTED,
}

# 协议 TaskState → 业务状态（终止态严格一致；特殊态降级 + 原文保留）
_PROTOCOL_TO_BUSINESS: Dict[TaskState, str] = {
    TaskState.TASK_STATE_SUBMITTED: "submitted",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_INPUT_REQUIRED: "input_required",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_CANCELED: "canceled",
    TaskState.TASK_STATE_REJECTED: "rejected",
}


def business_state_to_protocol(state: str) -> TaskState:
    """业务状态 → 协议 TaskState；未知业务状态抛 ValueError（不静默）。"""
    try:
        return _BUSINESS_TO_PROTOCOL[state]
    except KeyError:
        raise ValueError(f"未知业务状态: {state!r}") from None


def protocol_state_to_business(state: TaskState) -> str:
    """协议 TaskState → 业务状态。

    终止态严格映射；``AUTH_REQUIRED`` → ``input_required``、
    ``UNSPECIFIED``/未知状态 → ``working``（原状态经 ``TaskState.Name`` 由调用方
    写入 ``payload["protocol_state"]`` 保留）。
    """
    if state in _PROTOCOL_TO_BUSINESS:
        return _PROTOCOL_TO_BUSINESS[state]
    if state == TaskState.TASK_STATE_AUTH_REQUIRED:
        return "input_required"
    return "working"


def to_protocol_task(task: TaskStatus, state: TaskState) -> Task:
    """业务 TaskStatus → 协议 Task。

    ``trace_id`` / ``payload`` 写入 ``Task.metadata``（Struct）；其余协议字段
    （artifacts/history）保持默认空。
    """
    proto = Task(
        id=task.task_id,
        context_id=task.session_id,
        status=ProtocolTaskStatus(state=state),
    )
    # Struct 需整体 update（键不存在时下标访问抛 ValueError）
    meta: Dict[str, Any] = {}
    if task.trace_id:
        meta["trace_id"] = task.trace_id
    if task.payload:
        meta["payload"] = task.payload
    if meta:
        proto.metadata.update(meta)
    return proto


def from_protocol_task(task: Task) -> TaskStatus:
    """协议 Task → 业务 TaskStatus。

    ``trace_id`` 从 ``Task.metadata`` 读取（缺失回退 ``""`` 并标记）；
    降级状态（AUTH_REQUIRED/UNSPECIFIED）原文写入 ``payload["protocol_state"]``。
    """
    metadata: Dict[str, Any] = dict(task.metadata) if task.metadata else {}
    trace_id = str(metadata.get("trace_id", ""))
    raw_payload = metadata.get("payload")
    try:
        payload = dict(raw_payload or {})
    except (TypeError, ValueError):
        payload = {}  # payload 非 Struct（如 string）时容错为空

    protocol_state = task.status.state
    business_state = protocol_state_to_business(protocol_state)
    protocol_name = TaskState.Name(protocol_state)
    # 降级状态保留原文；否则不写（保持 payload 干净）
    if (
        protocol_state == TaskState.TASK_STATE_AUTH_REQUIRED
        or protocol_state == TaskState.TASK_STATE_UNSPECIFIED
        or protocol_state not in _PROTOCOL_TO_BUSINESS
    ):
        payload["protocol_state"] = protocol_name

    return TaskStatus(
        task_id=task.id,
        session_id=task.context_id,
        trace_id=trace_id,
        state=business_state,
        payload=payload,
    )
