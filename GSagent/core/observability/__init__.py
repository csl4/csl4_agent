"""业务与可观测对象模型（003-optimize-model-hierarchy）。

基于宪法【业务与可观测对象模型规范】落地：会话 → 任务 → Agent → 事件
四层对象 + 九类事件类型 + 指标快照。设计草案原见 GSDOC/agent1.md。

体系约定：
- 1 Task ↔ 1 Trace；一条 Trace 由多个可嵌套 Span 构成；Span 挂载瞬时 Event
- 指标（AgentMetrics/TaskMetrics）唯一来源是事件流聚合，禁止手工修改
- capture_full_content 默认关闭（隐私，OTel GenAI Layer4）
"""

from GSagent.core.observability.mapping import (
    business_state_to_protocol,
    from_protocol_task,
    protocol_state_to_business,
    to_protocol_task,
)
from GSagent.core.observability.metrics import MetricsAggregator
from GSagent.core.observability.models import (
    AgentEventEnvelope,
    AgentEventType,
    AgentMetrics,
    AgentStatus,
    LogLevel,
    SessionStatus,
    TaskMetrics,
    TaskStatus,
)
from GSagent.core.observability.cost import CostEstimator
from GSagent.core.observability.emitter import EventEmitter
from GSagent.core.observability.store import EventStore, MemoryEventStore

__all__ = [
    "AgentEventEnvelope",
    "AgentEventType",
    "AgentMetrics",
    "AgentStatus",
    "CostEstimator",
    "EventEmitter",
    "EventStore",
    "LogLevel",
    "MemoryEventStore",
    "MetricsAggregator",
    "SessionStatus",
    "TaskMetrics",
    "TaskStatus",
    "business_state_to_protocol",
    "from_protocol_task",
    "protocol_state_to_business",
    "to_protocol_task",
]
