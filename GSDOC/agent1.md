<!--
  [已落地] 本文件为业务与可观测对象模型的设计草案（历史记录）。
  正式实现以 `GSagent/core/observability/` 为准（specs/003-optimize-model-hierarchy），
  本文件保留作为设计演进依据，不再作为代码来源。
  主要差异：模型已转 Pydantic v2；事件类型扩展为九类；事件列表由 MemoryEventStore 承载；
  TaskStatus 增加 session_id；指标 frozen 仅事件流聚合。
-->
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class AgentEventType(Enum):
    # Agent生命周期事件，绑定 invoke_agent span
    AGENT_START = "agent.start"
    AGENT_END = "agent.end"
    AGENT_INTERRUPT = "agent.interrupt"
    # LLM调用事件，绑定 chat-llm client span
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"
    LLM_RETRY = "llm.retry"
    # 工具调用事件，绑定 execute_tool span
    TOOL_DECISION = "tool.decision"
    TOOL_CALL_START = "tool.call.start"
    TOOL_CALL_END = "tool.call.end"
    TOOL_ERROR = "tool.error"
    # 推理反思事件
    REASONING = "reasoning"
    PLANNING = "planning"
    REFLECTION = "reflection"
    # 上下文状态变更
    STATE_UPDATE = "state.update"
    CONTEXT_PRUNED = "context.pruned"
    # 异常恢复
    ERROR_RECOVERY = "error.recovery"
    FALLBACK_TRIGGERED = "fallback.triggered"


@dataclass
class AgentMetrics:
    """Agent维度聚合指标（快照，由事件流聚合生成，禁止手动修改）"""
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    llm_call_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    last_event_timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost_usd": self.total_cost_usd,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "error_count": self.error_count,
            "last_event_timestamp": self.last_event_timestamp
        }


@dataclass
class TaskMetrics:
    """Task任务维度聚合指标，汇总本任务所有Agent指标"""
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    total_error_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost_usd": self.total_cost_usd,
            "total_error_count": self.total_error_count
        }


@dataclass
class AgentEventEnvelope:
    """
    事件信封：Append-only 唯一数据源，对齐OTel GenAI规范
    每条事件归属一个span；span代表一段带起止时间的执行过程
    遵循隐私策略：完整prompt捕获为opt-in开关
    """
    schema_version: str = "1.0"

    # OTel链路追踪ID（仅标签，不是对象）
    trace_id: str = ""
    span_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_span_id: Optional[str] = None

    # A2A业务ID
    session_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    parent_agent_id: Optional[str] = None

    # 事件基础信息
    event_type: AgentEventType = AgentEventType.LLM_REQUEST
    level: LogLevel = LogLevel.INFO
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[float] = None

    # 计量数据，指标源头
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    message: str = ""
    # payload：默认不存储完整prompt，由 capture_full_content 开关控制（OTel Layer4）
    capture_full_content: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "event_type": self.event_type.value,
            "level": self.level.value,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "message": self.message,
            "capture_full_content": self.capture_full_content,
            "payload": self.payload
        }


@dataclass
class AgentStatus:
    """
    Agent业务实例快照（可变对象）
    Agent实例 != span；Agent每一轮turn，生成一条独立 invoke_agent span
    """
    agent_id: str
    task_id: str
    name: str
    agent_type: str
    parent_agent_id: Optional[str] = None

    is_running: bool = False
    # 当前正在执行的 invoke_agent span id
    current_span_id: Optional[str] = None
    # Agent私有上下文，和session全局消息隔离
    local_context: Dict[str, Any] = field(default_factory=dict)
    aggregated_metrics: AgentMetrics = field(default_factory=AgentMetrics)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "parent_agent_id": self.parent_agent_id,
            "is_running": self.is_running,
            "current_span_id": self.current_span_id,
            "local_context": self.local_context,
            "aggregated_metrics": self.aggregated_metrics.to_dict()
        }


@dataclass
class TaskStatus:
    """
    A2A Task业务对象，会话内独立业务目标
    约定：1 Task绑定1 trace_id，任务执行链路由多个嵌套Span组成
    """
    task_id: str
    trace_id: str

    agent_status_list: List[AgentStatus] = field(default_factory=list)
    aggregated_metrics: TaskMetrics = field(default_factory=TaskMetrics)

    # 【仅单机CLI调试】事件缓存；分布式环境直接删除该字段，事件写入外部存储
    event_list: List[AgentEventEnvelope] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "agent_status_list": [a.to_dict() for a in self.agent_status_list],
            "aggregated_metrics": self.aggregated_metrics.to_dict(),
            "event_count": len(self.event_list)
        }


@dataclass
class SessionStatus:
    """会话对象，A2A contextId，用户长期会话，支持多个Task"""
    session_id: str
    environment: str = "development"
    global_messages: List[Dict[str, Any]] = field(default_factory=list)
    task_list: List[TaskStatus] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "environment": self.environment,
            "global_messages": self.global_messages,
            "task_list": [t.to_dict() for t in self.task_list],
            "metadata": self.metadata
        }
