"""A2A 协议模型封装层——对接开源 a2a-sdk 1.0.0a2。

本模块**不重复实现协议**，而是把 `a2a.types` 的 protobuf 类型包装成项目内
稳定、易用的领域助手（宪法 V：复用开源框架、不自造协议轮子）。

SDK 事实（v1.0.0a2，protobuf 生成类型）：
- `Task` 字段：id / context_id / status(TaskStatus) / artifacts / history / metadata
- `TaskStatus` 字段：state(TaskState) / message(Message) / timestamp
- `TaskState` 枚举：SUBMITTED / WORKING / INPUT_REQUIRED / COMPLETED / FAILED / CANCELED / REJECTED / AUTH_REQUIRED
- `Message` 字段：message_id / context_id / task_id / role / parts / metadata / ...
- `Part` 字段：text / raw / url / data(google.protobuf.Value) / filename / media_type
"""

import datetime
from typing import Any, Dict, List, Optional

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToJson, Parse
from google.protobuf.timestamp_pb2 import Timestamp as ProtoTimestamp

# 复用 SDK 提供的协议类型（re-export 供本项目统一导入）
from a2a.types import (  # noqa: F401
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
)

# 终止态集合：到达后 Task 不再流转
TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)

# 失败/异常态（可用于调用方决定重试，宪法 V：重试走 tenacity）
FAILED_STATES = frozenset(
    {
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)


def task_state_name(state: TaskState) -> str:
    """返回 TaskState 的可读名称（如 TASK_STATE_COMPLETED）。"""
    return TaskState.Name(state)


def is_terminal(state: TaskState) -> bool:
    """该状态是否终止（completed/failed/canceled/rejected）。"""
    return state in TERMINAL_STATES


def is_failed(state: TaskState) -> bool:
    """该状态是否失败/异常（failed/canceled/rejected）。"""
    return state in FAILED_STATES


def _struct_from_dict(data: Dict[str, Any]) -> struct_pb2.Value:
    """把 dict 转成 protobuf Value（Part.data 需要）。"""
    value = struct_pb2.Value()
    value.struct_value.update(data)
    return value


def make_text_part(text: str) -> Part:
    """构造文本 Part。"""
    return Part(text=text)


def make_data_part(data: Dict[str, Any]) -> Part:
    """构造结构化数据 Part。"""
    return Part(data=_struct_from_dict(data))


def make_message(
    role: Role,
    text: str,
    task_id: str = "",
    message_id: str = "",
    context_id: str = "",
) -> Message:
    """构造一条 A2A Message（默认携带文本 Part）。"""
    return Message(
        message_id=message_id or f"msg-{Role.Name(role).lower()}",
        context_id=context_id,
        task_id=task_id,
        role=role,
        parts=[make_text_part(text)],
    )


def make_task(task_id: str, context_id: str = "") -> Task:
    """构造一个处于 SUBMITTED 状态的 Task。"""
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


def now_timestamp() -> ProtoTimestamp:
    """当前 UTC 时间的 protobuf Timestamp。"""
    ts = ProtoTimestamp()
    ts.FromDatetime(datetime.datetime.now(datetime.timezone.utc))
    return ts


def set_task_state(
    task: Task,
    state: TaskState,
    message_text: Optional[str] = None,
    timestamp: bool = True,
) -> Task:
    """更新 Task 状态；可选附带状态说明消息与时间戳。

    SDK 的 Task.status 是嵌套的 TaskStatus，直接在 status.state 上赋值即可。
    """
    task.status.state = state
    if timestamp:
        task.status.timestamp.CopyFrom(now_timestamp())
    if message_text is not None:
        task.status.message.CopyFrom(
            make_message(Role.ROLE_AGENT, message_text, task_id=task.id)
        )
    return task


def fail_task(task: Task, message: str) -> Task:
    """把 Task 置为 FAILED 并写失败消息（统一失败落地路径，C1 收敛）。

    客户端兜底 / 批量调度等各失败边界共用，避免各处重复手写 set_task_state。
    """
    return set_task_state(task, TaskState.TASK_STATE_FAILED, message)


def task_message_text(task: Task) -> str:
    """Task 状态消息的全部文本 Part 拼接（运行结果文本，区别于输入）。

    供 a2a.client 与 agents.base_agent 统一提取（B2 去重，单一来源）。
    """
    if not task.status.HasField("message"):
        return ""
    return "\n".join(p.text for p in task.status.message.parts if p.HasField("text"))


def task_to_json(task: Task) -> str:
    """Task 序列化为 JSON（调试/持久化）。"""
    return MessageToJson(task)


def copy_task(task: Task) -> Task:
    """深拷贝一个 Task（流式快照用，避免调用方持有同一可变对象）。"""
    dup = Task()
    dup.CopyFrom(task)
    return dup


def task_from_json(json_str: str) -> Task:
    """从 JSON 还原 Task。"""
    return Parse(json_str, Task())


def agent_card_for(
    name: str,
    description: str,
    skills: Optional[List[AgentSkill]] = None,
    version: str = "1.0.0",
) -> AgentCard:
    """为某个 Agent 角色构造 AgentCard（A2A 自描述/发现元数据）。"""
    return AgentCard(
        name=name,
        description=description,
        version=version,
        capabilities=AgentCapabilities(),
        skills=skills or [],
    )
