"""事件发射器：运行时埋点入口（接口预留）。

本期仅提供 emit → Store 写入的实现，**不接入** ToolCallingLLM/多 Agent 主循环
（plan.md R-07 scope 收缩）。后续增量埋点时，在 LLM/工具/审批/规划节点构造
AgentEventEnvelope 并调 emit 即可，不破坏本模块。
"""

from GSagent.core.observability.models import AgentEventEnvelope
from GSagent.core.observability.store import EventStore


class EventEmitter:
    """把事件信封原子写入事件仓库。"""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    @property
    def store(self) -> EventStore:
        """底层事件仓库（查询钻取入口）。"""
        return self._store

    def emit(self, event: AgentEventEnvelope) -> None:
        """发射一条事件（原子追加到仓库）。"""
        self._store.append(event)
