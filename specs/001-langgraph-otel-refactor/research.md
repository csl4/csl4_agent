# Research: LangGraph + OpenTelemetry 重构技术选型

> 阶段：Phase 0（/speckit-plan）｜ 日期：2026-09-13
> 对应 spec：`spec.md`（FR-001~FR-015）
> 方法：本地依赖树核实 + 现有代码结构分析 + OTel GenAI 语义约定 / LangGraph 1.x 公开实践。WebSearch 在当前环境不可用（需激活方舟控制台），关键版本兼容点以"实现阶段验证项"标注。

---

## R-01 决策：LLM 调用埋点方式

**Decision**: 采用「OpenLLMetry `opentelemetry-instrumentation-langchain`（自动埋点 LangGraph 图/节点/工具）+ `opentelemetry-instrumentation-litellm`（自动埋点 litellm 调用）+ 节点级手动业务 span」三层叠加，符合操作文档 6.3「双层结构（workflow 业务语义 + 自动 span 技术细节）」。

**Rationale**:
- 本地依赖树已确认：`langgraph 1.2.11`、`langchain-core 1.6.2`、`opentelemetry-sdk 1.42.1` / `exporter-otlp-proto-http` 均已存在（由 `langchain-cli` / `langsmith[otel]` / `langgraph-sdk` 传递引入），方案 A 的基础零额外 SDK 成本。
- 项目 LLM 调用走自定义 `LiteLLMProvider`（`litellm.completion` 直调），**不是** langchain 模型类——langchain instrumentor 的 `chat` span 不会覆盖它，必须为 litellm 单独埋点。`opentelemetry-instrumentation-litellm` 是 traceloop/OpenLLMetry 生态的官方 instrumentor，按 GenAI 语义约定输出 `gen_ai.operation.name=chat` 与 `gen_ai.usage.*`，与现有架构贴合度最高。
- 手动业务 span 承载节点语义（`node.*` / `route.decision`），与自动 span 组成瀑布图，满足宪法 13.1「步骤级 Thought 结构化摘要、计划变更」要求。

**Alternatives considered**:
1. **纯手动埋点（方案 D）**：在 `LiteLLMProvider` 内自写 span。完全可控、无新依赖，但需自行实现 token 提取/脱敏/错误处理，工作量大且易漏埋。
2. **迁移到 langchain 模型类（ChatOpenAI 等）**：天然被 langchain instrumentor 覆盖，但侵入现有 `LLM` 抽象与 litellm 供应商（`base_url`/`api_key`/多模型网关兼容）——违反宪法 2.1「换模型不动核心」，否决。
3. **仅 langchain instrumentor（方案 A 裸用）**：图/节点被埋，但 LLM 调用段空白，token/费用链路缺失——不满足 FR-002，否决。

> ⚠️ **实现阶段验证项 V-01**：`opentelemetry-instrumentation-litellm` 与当前 `litellm` 版本（1.x）的兼容性；若 instrumentor 缺失/不兼容，回退为在 `LiteLLMProvider.completion/completion_stream` 内手动包裹 span（同样输出 `gen_ai.*` 属性，零行为变化）。

---

## R-02 决策：LangGraph 图与 StreamMessage 契约的映射

**Decision**: **内部用 LangGraph StateGraph 重写编排，外部保持 `ToolCallingLLM.call_stream()` → `Generator[StreamMessage]` 契约完全不变**。`call_stream()` 内部遍历 `graph.stream()`（逐 super-step），把节点执行期间产生的业务事件映射为 `StreamMessage` 逐个 yield；`run_task()` 无头路径改用 `graph.invoke()`。

**Rationale**:
- 已核实外部契约消费方：`GSagent/main.py`（CLI `_run_turn`、chat 主循环）、`GSagent/core/agents/main_agent.py`（MainAgent 多 Agent 场景，`call_stream` 同签名）、`GSagent/core/runtime/server.py`（serve 后台线程）、`GSagent/core/plan/executor.py`（Plan 模式消费 StreamEvents）。任一契约破坏都会级联失效，且宪法 14.2「一切变更可回滚」要求用户可见行为不变。
- LangGraph `graph.stream(input, config)` 本身就是生成器，天然适配 `call_stream` 的生成器语义，无需额外缓冲线程。
- 事件产生位置：节点执行期间（LLM 调用前后、工具调用前后）通过注入的事件收集器缓冲 `AgentEventEnvelope` 与 `StreamMessage`，super-step 边界处 flush 给调用方——保持流式/可打断（`cancel_event`）体验。

**Alternatives considered**:
1. **用 LangGraph 直接替代 StreamMessage 流**（图执行结果一次性返回）：改动最小但破坏 `StreamMessage` SSE 契约，CLI/serve 全部返工——否决。
2. **手写循环内部换图、外部包一层异步队列**：过度设计，`graph.stream` 已天然满足——否决。

---

## R-03 决策：中断/恢复语义的映射（实施期修订）

**Decision**: 采用 langgraph **`interrupt()`** 承载审批/前端暂停（人在回环），恢复用 **`Command(resume=...)` + checkpointer（thread_id 固定）**真正断点续跑。外部 `call_stream` 契约不变：暂停 return，下次以 `tool_decisions`/`frontend_tool_results` 接续——内部映射为 `Command(resume=...)`。

**Rationale**:
- 用户要求彻底 langgraph 化。`interrupt()` 是 langgraph 官方人在回环机制，替代了原先手写的 `pause/terminated/tool_decisions` 状态字段重入。
- checkpointer 用固定 `thread_id`（agent_id 或 request_context.thread_id），暂停后下次 `Command(resume=...)` 从断点继续，**不重复执行已完成节点**（SC-005）。
- 熔断/取消/输入拦截（非暂停）仍走 `terminated` 字段 + 条件边，不走 interrupt。

**Rationale**:
- 现有语义：`call_stream(..., tool_decisions=..., frontend_tool_results=...)` 在**下次调用**时接续，无进程内持久化。这已被 CLI（`main.py` chat 循环）与 serve（`server.py` 回合）消费。
- LangGraph `interrupt()` 的恢复依赖 `Command(resume=...)` 或 `update_state`，会改变现有调用签名，且与「下次 call_stream 传参恢复」的既有消费方式冲突。
- 折中方案：图状态（`GraphState`）显式包含 `pending_approvals` / `frontend_results` / `paused` 字段。节点在审批/前端暂停时**提前返回**（图走 `END` 或条件边挂起），状态保存在 checkpointer；下次 `call_stream` 传入决策时，用 `update_state` 写入决策并重启图，从对应节点继续。行为与现状一致，仅把「隐式恢复」变为「checkpointer + 状态重入」。
- `cancel_event`（用户取消）映射为节点前的取消检查（现有 `cancel_event.is_set()` 逻辑保留）。

> ⚠️ **实现阶段验证项 V-02**：LangGraph 1.2.x 的 `update_state` + 重启图在「同 thread_id 继续」模式下的行为，验证恢复后不重复执行已完成节点（spec SC-005）。

---

## R-04 决策：trace_id 事件流传播

**Decision**: `AgentEventEnvelope.trace_id / span_id / parent_span_id` 从 OTel 当前 span context 提取；节点内事件构造处通过 `opentelemetry.trace.get_current_span().get_span_context()` 读取并回填信封；未接入 OTel 时保持空串（零依赖可运行，FR-010）。

**Rationale**:
- 宪法 13.1「任务级/步骤级/工具调用级三层 Trace，全局 trace_id 贯穿」要求事件信封与 OTel span 同源。
- 事件信封（`models.py`）字段已预留 `trace_id/span_id/parent_span_id`，仅缺接线——R-04 是纯增量。
- 图每次 `invoke_agent` 外层包一个任务级 span（`gen_ai.operation.name=invoke_agent`），节点内 LLM/工具 span 由自动埋点生成，事件从各自当前 span 提取 ID，三者同源可串联。

---

## R-05 决策：Guardrail 输入/输出侧设计

**Decision**: 在 `GSagent/core/policy/` 新增 `input_guard.py`（输入侧）与 `output_guard.py`（输出侧），复用既有 `AuditLog` 留痕；输入侧接入编排图入口节点，输出侧接入最终答案出口。结果侧沿用 `ToolExecutor` 内既有 `path_guard / command_guard` 校验（已实现）。

**Rationale**:
- 宪法 11.1 三道 Guardrail：输入侧（违禁词/Prompt Injection/内容合规）、输出侧（结构校验/敏感过滤/幻觉降级）、结果侧（工具返回值校验）。当前**结果侧已有**（`executor.py` 守卫 + `rules.py` 分类），输入/输出侧缺失。
- 输入侧用「规则匹配（注入模式 + 违禁词）+ 可选 LLM 校验」两级；命中即审计 `blocked` 并中止该轮，符合宪法「确定性交给代码」。
- 输出侧对**程序消费的输出**（如 `response_format` 结构化场景）做 schema 校验，失败走宪法 5.4 分层 Fallback（轻量修复 → 定向重试 → 降级人工），不直接抛错。
- 全部拦截复用 `AuditLog.record(event_type=...)`，与既有审计通道一致（FR-006/007）。

**Alternatives considered**:
1. **Guardrail 全部并入 ToolExecutor**：输入侧不经过工具层（面向 LLM 输入而非工具入参），职责错位——否决。
2. **新建独立 guardrail/ 包**：与既有 `policy/` 语义重叠，宪法 II「单一职责、复用既有」——否决，并入 `policy/`。

---

## R-06 决策：依赖新增

**Decision**: 新增 `opentelemetry-instrumentation-langchain` 与 `opentelemetry-instrumentation-litellm`（均 ≥0.55，满足 GenAI 语义约定）；可选 `opentelemetry-instrumentation-httpx`（HTTP 层串联）与 `opentelemetry-instrumentation-fastapi`（serve 入口串联，操作文档 12 异步应用建议）。OTel SDK 本体已存在，无需重复声明。

**Rationale**:
- OTel SDK 由 `langchain-cli` / `langsmith[otel]` 传递引入（uv.lock 核实），直接复用。
- instrumentors 走 `uv pip install` 进当前环境（base_llm），与项目既有依赖管理一致（CLAUDE.md）。
- 新增依赖为**可选运行时增强**：未安装时 OTel 初始化模块应优雅降级（try-import + 开关），不阻塞主流程（FR-010）。

**Alternatives considered**: 全手动埋点零新依赖（见 R-01 alt-1）——已否决，自动埋点是操作文档推荐方案。

---

## 决策汇总

| # | 决策 | 落实 FR |
|---|---|---|
| R-01 | OpenLLMetry 自动埋点（langchain + litellm）+ 手动业务 span 三层叠加 | FR-002/014 |
| R-02 | 内部 LangGraph StateGraph，外部 `call_stream`/`run_task` 契约不变 | FR-003/011 |
| R-03 | checkpointer + 状态字段承载暂停/恢复，不引入 interrupt 重写 | FR-004/005/SC-005 |
| R-04 | 事件信封 trace/span ID 取自 OTel 当前 span context，未接 OTel 留空 | FR-001/SC-001 |
| R-05 | 新增 input/output Guardrail 入 `policy/`，复用 AuditLog，结果侧沿用既有 | FR-006/007 |
| R-06 | 新增 2 个 instrumentors（+2 可选），OTel SDK 复用传递依赖 | FR-009/010 |

**验证项汇总**：V-01（litellm instrumentor 兼容）、V-02（checkpointer 恢复行为）。两者均在实现阶段首日验证，若受阻即启用对应回退方案。

### 验证结论（2026-09-13，实现阶段 T010）

- **V-01 litellm instrumentor 兼容**：⚠️ **未能实测**。当前环境 `base_llm` 的 site-packages 对普通用户只读（`icacls` 需管理员授权），`opentelemetry-instrumentation-litellm` 安装被拒（`os error 5`）。实现已采取**双保险**：`telemetry._instrument()` try-import 自动埋点，失败则优雅降级为仅手动业务 span；无论 instrumentor 是否可用，主流程不阻塞（FR-010）。待管理员授权安装后补测。
- **V-02 checkpointer 恢复行为**：✅ **已实测**。实施期修订为 `interrupt()` + `Command(resume=...)` + 固定 thread_id 真正断点恢复。`tests/unit/orchestration/test_pause_resume.py` 验证：审批暂停 → 下次 `tool_decisions` 接续 → 从断点续跑且工具结果不丢失；`test_call_stream_contract.py` 验证非暂停路径回归。
