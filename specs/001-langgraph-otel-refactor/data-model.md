# Data Model: LangGraph + OTel 重构

> 阶段：Phase 1（/speckit-plan）｜ 日期：2026-09-13
> 对应 spec：`spec.md` ｜ 设计：`research.md` R-02/R-03/R-04

---

## 1. 编排图状态（LangGraph GraphState）

编排层新增状态 schema，承载消息、工具执行、暂停/恢复与事件缓冲。

```python
# GSagent/core/orchestration/state.py（设计草案）
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages  # 追加式合并（操作文档 2 Reducer）

class GraphState(TypedDict):
    # 对话消息（OpenAI 格式 dict 列表），追加式合并
    messages: Annotated[List[Dict[str, Any]], add_messages]

    # 工具审批：本轮待确认的调用（tool_call_id → 参数/名称/原因）
    pending_approvals: List[Dict[str, Any]]
    # 用户决策回填：tool_call_id/tool_name → True(批准)/False(拒绝)
    tool_decisions: Dict[str, bool]
    # 前端工具结果回填：tool_call_id → 结果
    frontend_tool_results: Dict[str, Any]

    # 暂停语义（图提前结束的信号）
    pause: Optional[str]            # None | "approval" | "frontend"
    # 是否强制终止（步数熔断/取消）
    terminated: Optional[str]       # None | "max_steps" | "cancelled"

    # 编排元信息（可观测）
    iteration: int                  # 当前步数（LLM 调用计数）
    tool_number: int                # 工具编号偏移
    last_tool_calls: List[Dict[str, Any]]  # 死循环检测（连续 3 步相同调用）
    no_progress_streak: int         # 连续无进展步数（宪法 10.1 软干预）

    # 事件缓冲：节点执行期间产生的可观测事件（super-step 边界 flush）
    #   由 call_stream 适配器消费，不属于 LLM 可见上下文
    _events: List[Dict[str, Any]]
    _stream_messages: List[Dict[str, Any]]
```

**字段约束与转移规则**：
- `messages` 为唯一 LLM 可见上下文，`_events`/`_stream_messages` 是编排内部缓冲（前缀 `_` 标记，不在图间传播给 LLM）。
- `pause` 到达 `approval`/`frontend` 时图提前返回；下次 `call_stream(tool_decisions=...)` 通过 `update_state` 写入决策后从对应节点继续（R-03）。
- `iteration` 上限 = `agent.max_steps`（默认 20）；`last_tool_calls` 连续 3 步与上一轮完全相同 → `terminated="max_steps"`（宪法 2.3 / 附录 A）。

---

## 2. 新增配置段（config.yaml / 环境变量）

### `observability:`（OTel 开关，默认关闭）

```yaml
observability:
  enabled: false            # 总开关；false = 零 OTel 初始化，主流程无感知（FR-010）
  service_name: "gsagent"   # OTel service.name（默认 gsagent）
  otlp_endpoint: ""         # OTLP 导出地址；空 = http://localhost:4318
  trace_content: false      # 是否采集 prompt/completion（默认关，FR-007 / 操作文档 6.2）
  sample_ratio: 1.0         # 采样率（高流量可降，操作文档 12）
```

环境变量：`AGENT_OTEL_ENABLED`（bool）、`AGENT_OTEL_ENDPOINT`、`AGENT_OTEL_SERVICE_NAME`、`AGENT_OTEL_TRACE_CONTENT`。新增 `_ENV_OVERRIDES` 声明式表条目（宪法 III：只增不改既有字段）。

### `guardrails:`（输入/输出侧，默认全部启用）

```yaml
guardrails:
  input:
    enabled: true           # 输入侧拦截（注入/内容合规）
    deny_patterns: []       # 追加违禁/注入模式（正则，叠加入口内置默认）
  output:
    enabled: true           # 输出侧校验（结构化场景）
    validate_schema: true   # 对程序消费输出做 schema 校验
    fallback_retries: 1     # 分层 Fallback 定向重试次数（宪法 5.4，≤2）
```

环境变量：`AGENT_GUARDRAIL_INPUT`、`AGENT_GUARDRAIL_OUTPUT`（bool）。默认启用、可关——不改变既有未配置行为。

---

## 3. 事件模型映射（复用既有，仅接线）

既有 `GSagent/core/observability/models.py` **不改 schema**，本次只补接线：

| 信封字段 | 来源（R-04） |
|---|---|
| `trace_id` / `span_id` / `parent_span_id` | OTel 当前 span context（`get_current_span().get_span_context()`）；未接 OTel 留空 |
| `session_id` / `task_id` / `agent_id` / `parent_agent_id` | 编排上下文（`request_context` + Agent 身份，与 audit_mixin 同源） |
| `event_type` / `level` / `timestamp` / `duration_ms` | 节点事件构造（既有 `AgentEventType` 九类枚举） |
| `tokens_in` / `tokens_out` / `cost_usd` | LLM 响应 `usage` + `CostEstimator`（既有 `_usage_with_cost` 逻辑迁移/复用） |
| `payload` / `capture_full_content` | `capture_full_content` 默认 False；`payload_redacted()` 脱敏 |

**事件 → span 归属**（宪法 13.1 三层，操作文档 6.1）：
- `AGENT_START/END/INTERRUPT` → 外层任务 span（`gen_ai.operation.name=invoke_agent`，手动）
- `LLM_REQUEST/RESPONSE/ERROR/RETRY` → litellm chat span（自动，`gen_ai.operation.name=chat`）
- `TOOL_DECISION/CALL_START/CALL_END/ERROR` → 工具 span（自动 + 手动叠加）
- `REASONING/PLANNING/REFLECTION/STATE_UPDATE/CONTEXT_PRUNED` → 节点业务 span（手动，`node.*`）

**指标聚合**：`MetricsAggregator` 从事件流聚合 `AgentMetrics`/`TaskMetrics`（frozen 快照）——既有实现不变，本次只是让事件真正产生。

---

## 4. Guardrail 数据模型

### 输入侧（`input_guard.py`）

```python
class GuardResult(BaseModel):
    allowed: bool
    reason: str = ""          # 拦截原因（审计用）
    matched_rule: str = ""    # 命中的规则 id
    sanitized: Optional[str] = None  # 可选：脱敏后的输入
```

- 规则集：注入模式（如角色逃逸/系统指令覆盖模式）+ 违禁词 + 用户追加 `deny_patterns`。
- 命中 → `allowed=False`，`AuditLog.record(event_type="guardrail_input", outcome="blocked")`，中止本轮 LLM 调用。
- 未命中 → `allowed=True`，原样放行（零改动既有输入）。

### 输出侧（`output_guard.py`）

```python
class OutputCheckResult(BaseModel):
    ok: bool
    issue: str = ""                  # 校验失败描述
    fallback_applied: bool = False   # 是否已执行轻量修复
    final_content: str = ""          # 修复后内容（若应用）
```

- 仅对**程序消费的结构化输出**（`response_format` 场景）做 schema 校验；自由文本输出跳过（不误伤）。
- 失败走分层 Fallback：轻量修复 → 带错误上下文定向重试（≤2）→ 降级（标记 `fallback_applied`），不直接抛错（宪法 5.4）。
- 敏感信息过滤复用 `payload_redacted` 语义（token/key/secret 等键名）。

---

## 5. 实体关系总览

```text
SessionStatus ──1:N── TaskStatus ──1:1── trace_id
                        │  1:N
                     AgentStatus ──1:N── AgentEventEnvelope（trace/span_id 取自 OTel）
                        │
                        └── aggregated: AgentMetrics（frozen，事件流聚合唯一来源）
LangGraph GraphState（编排内部）──产出──▶ StreamMessage 事件流（外部契约，不变）
                                          └── 同步构造 AgentEventEnvelope 入 MemoryEventStore
```
