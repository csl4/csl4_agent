"""事件仓库：单机 Append-Only（MemoryEventStore）+ 存储抽象（EventStore）。

宪法对象模型规范：单机内存可缓存 event_list 用于回放调试；分布式部署删除
事件列表、事件异步输出至外部时序存储。`EventStore` 是契约抽象——换分布式
实现不改业务模型与查询语义（spec FR-005，本期仅落地单机，分布式为后续增量）。
"""

import threading
from datetime import datetime
from typing import List, Optional, Protocol, runtime_checkable

from GSagent.core.observability.models import AgentEventEnvelope, AgentEventType


def _event_key(ts: str) -> tuple:
    """解析事件时间戳为可排序 key（ISO 字符串 → datetime 元组）；解析失败回退原串。"""
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return ts  # 容错：非标准时间戳按原字符串排序


@runtime_checkable
class EventStore(Protocol):
    """事件存储契约（单机/分布式实现通用）。"""

    def append(self, event: AgentEventEnvelope) -> None: ...

    def query(
        self,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        event_type: Optional[AgentEventType] = None,
        limit: Optional[int] = None,
    ) -> List[AgentEventEnvelope]: ...

    def replay(self, task_id: str) -> List[AgentEventEnvelope]: ...

    def clear(self) -> None: ...


class MemoryEventStore:
    """单机内存事件仓库：原子追加 + 条件查询 + 时间升序 + 深拷贝快照。

    查询返回深拷贝（外部修改不影响仓库）；append 用 Lock 保证并发安全。
    千级事件规模下过滤查询远低于 500ms 目标（spec SC-002）。
    """

    def __init__(self) -> None:
        self._events: List[AgentEventEnvelope] = []
        self._lock = threading.Lock()

    def append(self, event: AgentEventEnvelope) -> None:
        """原子追加一条事件（按追加顺序入全局列表，查询时按时间排序）。"""
        with self._lock:
            self._events.append(event)

    def query(
        self,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        event_type: Optional[AgentEventType] = None,
        limit: Optional[int] = None,
    ) -> List[AgentEventEnvelope]:
        """按条件过滤 + 时间升序，返回深拷贝快照。"""
        with self._lock:
            candidates = list(self._events)
        filtered = [
            e
            for e in candidates
            if (session_id is None or e.session_id == session_id)
            and (task_id is None or e.task_id == task_id)
            and (agent_id is None or e.agent_id == agent_id)
            and (event_type is None or e.event_type is event_type)
        ]
        filtered.sort(key=lambda e: _event_key(e.timestamp))
        if limit is not None:
            filtered = filtered[:limit]
        return [e.model_copy(deep=True) for e in filtered]

    def replay(self, task_id: str) -> List[AgentEventEnvelope]:
        """单任务完整事件序列（时间升序），供回放调试（spec US1）。"""
        return self.query(task_id=task_id)

    def clear(self) -> None:
        """清空全部事件（测试/重置用）。"""
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        """事件总数（查询辅助）。"""
        with self._lock:
            return len(self._events)
