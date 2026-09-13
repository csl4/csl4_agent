# Contract: 可观测层（OpenTelemetry + 事件流）

> 来源：`spec.md` FR-001/002/007/009/010/014 ｜ 设计：`research.md` R-01/R-04/R-06 ｜ 数据模型：`data-model.md` §3

## 目的

定义 OTel 接入与事件流接线的稳定契约：自动埋点 + 手动业务 span 双层结构、GenAI 语义约定、`AgentEventEnvelope` 从 OTel context 取链路 ID、后端厂商中立、主流程不依赖可观测。

## 1. Telemetry 初始化契约

`GSagent/core/observability/telemetry.py` 暴露单一入口 `setup_telemetry(config) -> Tracer`：

- **触发**：`observability.enabled=true` 时由 `Config` 装配调用（`config.py`）；默认关闭 = 零初始化（FR-010）。
- **资源属性**：`service.name`（`observability.service_name`）、`service.version`（取包版本）、`deployment.environment`（`AGENT_ENV`，默认 `development`）。
- **Span processor**：`BatchSpanProcessor`（异步批量，操作文档 12，禁止 `SimpleSpanProcessor`）；`atexit`/`shutdown` flush。
- **导出**：`OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")`；`otlp_endpoint` 空 → `http://localhost:4318`（操作文档 5.1 Jaeger 默认）。
- **自动埋点**：`LangchainInstrumentor().instrument()`（LangGraph 图/节点）+ litellm instrumentor（LLM 调用）。**版本验证 V-01**。
- **内容开关**：`trace_content=false` 时不采集 prompt/completion（映射 `TRACELOOP_TRACE_CONTENT=false` 语义，操作文档 6.2）；SDK 默认也仅当 `capture_full_content=true` 才写 `gen_ai.prompt/completion`。
- **优雅降级**：instrumentors 未安装/导入失败 → 记录 warning，仅保留手动业务 span（FR-010，可观测失败不阻塞主流程）。

## 2. Span 结构与属性契约（GenAI 语义约定）

| Span | 来源 | 属性（语义约定） |
|---|---|---|
| 任务级 `invoke_agent` | 手动（`call_stream`/`run_task` 外层包裹） | `gen_ai.operation.name=invoke_agent`、`langgraph.node`、`session_id`、`task_id` |
| 节点业务 span `node.*` | 手动（节点装饰器，操作文档 9 方案 D） | `langgraph.node`、`route.decision`、`node.output_keys` |
| LLM 调用 `chat` | 自动（litellm instrumentor） | `gen_ai.operation.name=chat`、`gen_ai.model`、`gen_ai.usage.input_tokens/output_tokens` |
| 工具调用 `execute_tool` | 自动（langchain tool instrumentor） | `gen_ai.operation.name=execute_tool`、`langchain.tool.name` |

**费用属性**：手动在 LLM span 上记录 `llm.cost.usd`（用既有 `CostEstimator`，R-01 record_cost 模式）；无定价条目不写（与现状"只记 token"一致）。

## 3. 事件信封接线契约

`EventEmitter.emit(AgentEventEnvelope)` 在**节点内**构造并写入：

- `trace_id` / `span_id` / `parent_span_id`：取 `opentelemetry.trace.get_current_span().get_span_context()`；未接 OTel（enabled=false）→ 留空串（模型兼容，FR-010）。
- `session_id` / `task_id` / `agent_id` / `parent_agent_id`：`request_context` + Agent 身份（复用 `AuditUsageMixin._session_id` 同源）。
- `tokens_in/out` / `cost_usd`：LLM 响应 `usage` + `CostEstimator.estimate`。
- `capture_full_content`：默认 False；仅 `trace_content=true` 且显式开启时写 payload 完整内容（FR-007）。
- 敏感键：`payload_redacted()` 递归剔除（token/key/secret 等，既有实现）。

**存储**：`MemoryEventStore`（既有），`MetricsAggregator` 聚合 `AgentMetrics`/`TaskMetrics`（frozen 快照，唯一来源）。

## 4. 事件 → span 归属映射

| AgentEventType | 归属 span | 埋点来源 |
|---|---|---|
| `AGENT_START/END/INTERRUPT` | `invoke_agent`（任务级） | 手动 |
| `LLM_REQUEST/RESPONSE/ERROR/RETRY` | `chat`（LLM） | 自动（litellm） |
| `TOOL_DECISION/CALL_START/CALL_END/ERROR` | `execute_tool`（工具） | 自动 + 手动叠加 |
| `REASONING/PLANNING/REFLECTION/STATE_UPDATE/CONTEXT_PRUNED` | `node.*`（节点级） | 手动 |
| `APPROVAL_REQUIRED/DECISION`、`A2A_*`、`PLAN_*` | 对应节点/交接 span | 手动 |

## 5. 厂商中立与可替换（FR-009）

- 应用只向 `otlp_endpoint` 发 OTLP，不感知后端身份（Jaeger/Tempo/LangSmith/Phoenix 任选，操作文档 1）。
- 可选 Collector 中间层：`otel-collector-config.yaml` 承担采样/脱敏/多路分发（操作文档 10，含 `transform/redact` 替换 `gen_ai.prompt/completion` 示例）。
- 切换后端 = 改 `observability.otlp_endpoint` 或 Collector 配置，**不涉及 Agent 主流程代码**。

## 6. 验收要点

- `observability.enabled=false`：零 OTel 初始化，主流程与既有行为完全一致。
- enabled=true + 本地 Jaeger：运行后能在 Jaeger 看到完整瀑布（`invoke_agent → chat/execute_tool`），LLM span 带 `gen_ai.usage.*` 与 `llm.cost.usd`（SC-001/002）。
- `trace_content=false`：Jaeger span 无 prompt/completion 内容，token 指标仍在（内容与指标解耦）。
- Jaeger 不可达：Agent 正常完成，仅导出线程报错（FR-010）。
