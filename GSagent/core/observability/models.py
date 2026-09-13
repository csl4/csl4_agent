"""业务与可观测对象模型：四层对象 + 九类事件 + 指标快照（Pydantic v2）。

基于宪法【业务与可观测对象模型规范】与 GSDOC/agent1.md 草案落地。
设计要点：
- 指标快照（AgentMetrics/TaskMetrics）用 `frozen=True`——实例不可变、无 setter，
  唯一来源是事件流聚合（metrics.py MetricsAggregator），宪法硬约束
- 隐私：`capture_full_content` 默认 False（OTel Layer4），序列化不含完整内容；
  `payload_redacted()` 递归剔除敏感键
- 向后兼容：所有字段带默认值，新枚举成员为增量，既有构造零迁移（宪法 III 精神）
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

SENSITIVE_KEYS = ("token", "key", "password", "secret", "authorization", "bearer")


class LogLevel(Enum):
    """事件日志级别。"""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class AgentEventType(Enum):
    """事件类型（九类，覆盖关键生命周期）。

    三类新增（审批 / A2A 消息 / 规划）对齐既有运行时事件：
    APPROVAL_REQUIRED 审批流、多 Agent 调度、plan 模式（spec FR-007）。
    """

    # Agent 生命周期（绑定 invoke_agent span）
    AGENT_START = "agent.start"
    AGENT_END = "agent.end"
    AGENT_INTERRUPT = "agent.interrupt"
    # LLM 调用（绑定 chat-llm span）
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"
    LLM_RETRY = "llm.retry"
    # 工具调用（绑定 execute_tool span）
    TOOL_DECISION = "tool.decision"
    TOOL_CALL_START = "tool.call.start"
    TOOL_CALL_END = "tool.call.end"
    TOOL_ERROR = "tool.error"
    # 推理反思
    REASONING = "reasoning"
    PLANNING = "planning"
    REFLECTION = "reflection"
    # 上下文状态变更
    STATE_UPDATE = "state.update"
    CONTEXT_PRUNED = "context.pruned"
    # 异常恢复
    ERROR_RECOVERY = "error.recovery"
    FALLBACK_TRIGGERED = "fallback.triggered"
    # 审批（新增，对齐 APPROVAL_REQUIRED 审批流）
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_DECISION = "approval.decision"
    # A2A 消息（新增，对齐多 Agent 调度）
    A2A_MESSAGE_SENT = "a2a.message.sent"
    A2A_MESSAGE_RECEIVED = "a2a.message.received"
    # 规划（新增，对齐 plan 模式）
    PLAN_GENERATED = "plan.generated"
    PLAN_TASK_STARTED = "plan.task.started"
    PLAN_TASK_DONE = "plan.task.done"


# ---- 指标快照（只读，唯一来源是事件流聚合） ----

class AgentMetrics(BaseModel):
    """Agent 维度聚合指标（快照，由 MetricsAggregator 从事件流生成）。"""

    model_config = ConfigDict(frozen=True)

    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    llm_call_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    last_event_timestamp: Optional[str] = None


class TaskMetrics(BaseModel):
    """Task 维度聚合指标（快照，汇总本任务所有 Agent 指标）。"""

    model_config = ConfigDict(frozen=True)

    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    total_error_count: int = 0


def payload_redacted(payload: Dict[str, Any]) -> Dict[str, Any]:
    """递归剔除 payload 中键名含敏感词的条目（脱敏辅助，对齐审计脱敏语义）。

    ``token/key/password/secret/authorization/bearer`` 键名的值一律剔除。
    返回脱敏后的新 dict，不改原 payload。
    """
    if not isinstance(payload, dict):
        return payload
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        low = str(k).lower()
        if any(s in low for s in SENSITIVE_KEYS):
            continue
        if isinstance(v, dict):
            out[k] = payload_redacted(v)
        else:
            out[k] = v
    return out


# ---- 事件信封（Append-Only 唯一数据源） ----

class AgentEventEnvelope(BaseModel):
    """事件信封：Append-Only 唯一数据源，指标与链路溯源的原始来源。

    每条事件归属一个 Span；Span 代表一段带起止时间的执行过程。
    隐私：capture_full_content 默认关闭，不存储完整 prompt/completion（OTel Layer4）。
    """

    schema_version: str = "1.0"

    # OTel 链路标签（仅 ID 标签集合，非业务对象）
    trace_id: str = ""
    span_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_span_id: Optional[str] = None

    # A2A 业务 ID
    session_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    parent_agent_id: Optional[str] = None

    # 事件基础信息
    event_type: AgentEventType = AgentEventType.LLM_REQUEST
    level: LogLevel = LogLevel.INFO
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[float] = None

    # 计量数据（指标源头）
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    message: str = ""
    # payload：默认不存完整 prompt/completion，由 capture_full_content 控制
    capture_full_content: bool = False
    payload: Dict[str, Any] = Field(default_factory=dict)

    def redacted_payload(self) -> Dict[str, Any]:
        """返回脱敏后的 payload（不修改原对象；capture_full_content=False 时为空承载）。"""
        return payload_redacted(self.payload)


# ---- 三层状态快照：Session → Task → Agent ----

class AgentStatus(BaseModel):
    """Agent 业务实例快照。

    Agent 实例 ≠ Span；Agent 每一轮 turn 执行新建一条 ``invoke_agent`` Span。
    """

    agent_id: str
    task_id: str
    name: str
    agent_type: str
    parent_agent_id: Optional[str] = None

    is_running: bool = False
    # 当前正在执行的 invoke_agent span id
    current_span_id: Optional[str] = None
    # Agent 私有上下文，与 session 全局消息隔离
    local_context: Dict[str, Any] = Field(default_factory=dict)
    aggregated_metrics: AgentMetrics = Field(default_factory=AgentMetrics)


# 任务业务状态（状态机见 data-model.md）
TASK_STATES = frozenset(
    {"submitted", "working", "input_required", "completed", "failed", "canceled", "rejected"}
)
# 终止态：到达后不再流转
TERMINAL_TASK_STATES = frozenset({"completed", "failed", "canceled", "rejected"})


class TaskStatus(BaseModel):
    """业务任务对象（A2A taskId），会话内独立业务目标。

    约定：1 Task 绑定 1 trace_id；任务执行链路由多个嵌套 Span 组成。
    事件列表由 MemoryEventStore 承载（单机），本对象不内嵌事件实体（R-05）。
    """

    task_id: str
    session_id: str = ""
    trace_id: str
    state: str = "submitted"

    agent_status_list: List[AgentStatus] = Field(default_factory=list)
    aggregated_metrics: TaskMetrics = Field(default_factory=TaskMetrics)
    # 厂商扩展字段（不污染顶层 schema）
    payload: Dict[str, Any] = Field(default_factory=dict)


class SessionStatus(BaseModel):
    """会话对象（A2A contextId），用户长期会话，可包含多个任务。"""

    session_id: str
    environment: str = "development"
    global_messages: List[Dict[str, Any]] = Field(default_factory=list)
    task_list: List[TaskStatus] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
