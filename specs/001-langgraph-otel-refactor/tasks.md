---

description: "任务清单：LangGraph 编排 + OpenTelemetry 可观测重构（宪法对齐）"

---

# Tasks: LangGraph 编排 + OpenTelemetry 可观测重构（宪法对齐）

**Input**: Design documents from `/specs/001-langgraph-otel-refactor/`

**Prerequisites**: plan.md（已读）、spec.md（已读）、research.md、data-model.md、contracts/

**Tests**: 本项目 CLAUDE.md/宪法 IV 硬性要求「新功能需要单元测试（Test-First）」，故每个用户故事包含测试任务，且测试先于实现编写。

**Organization**: 任务按用户故事分组。注意依赖关系——**US1/US3 均需挂在 LangGraph 编排图上**，故编排图骨架放入 Foundational 阶段（阻塞全部 US），US2 在骨架之上完成完整能力（暂停/恢复/熔断）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- 单项目仓库：源码 `GSagent/`，测试 `tests/`（镜像 `GSagent/` 结构；当前 tests/ 迁移中，实现时按 `tests/unit/orchestration/` 等补齐）

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 依赖声明、配置段预留、编排层包结构

- [X] T001 在 `pyproject.toml` 的 `[project]` dependencies 增加 `opentelemetry-instrumentation-langchain>=0.55` 与 `opentelemetry-instrumentation-litellm>=0.55`（可选：`opentelemetry-instrumentation-httpx`、`opentelemetry-instrumentation-fastapi` 放 `server` 组），并执行 `uv pip install -e ".[dev]"` 验证
- [X] T002 [P] 在 `GSagent/config.py` 增加 `observability`（enabled/service_name/otlp_endpoint/trace_content/sample_ratio）与 `guardrails`（input.enabled/deny_patterns、output.enabled/validate_schema/fallback_retries）配置段到 `DEFAULT_CONFIG`，并注册 `AGENT_OTEL_ENABLED`、`AGENT_OTEL_ENDPOINT`、`AGENT_OTEL_SERVICE_NAME`、`AGENT_OTEL_TRACE_CONTENT`、`AGENT_GUARDRAIL_INPUT`、`AGENT_GUARDRAIL_OUTPUT` 到 `_ENV_OVERRIDES`（宪法 III：只增不改既有字段）
- [X] T003 [P] 创建 `GSagent/core/orchestration/__init__.py` 包结构（空导出占位，宪法 2.1 编排层独立）

---

## Phase 2: Foundational（阻塞全部用户故事）—— LangGraph 图骨架 + 外部契约适配

**Purpose**: 核心编排基础设施，**未完成前任何用户故事不可开始**（US1/US3 需挂在图上，US2 在其上扩展）

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 实现 `GraphState` 于 `GSagent/core/orchestration/state.py`：messages（`add_messages` 追加合并）、pending_approvals、tool_decisions、frontend_tool_results、pause、terminated、iteration、tool_number、last_tool_calls、no_progress_streak、`_events`/`_stream_messages` 缓冲字段（按 data-model.md §1）
- [X] T005 实现 `setup_telemetry()` 于 `GSagent/core/observability/telemetry.py`：TracerProvider + BatchSpanProcessor + OTLPSpanExporter，`enabled=false` 时零初始化，instrumentors try-import 优雅降级（contracts/observability.md §1，FR-010）
- [X] T006 实现图组装于 `GSagent/core/orchestration/graph.py`：`StateGraph(GraphState)` 注册节点、`should_continue` 条件边、`compile(checkpointer=InMemorySaver())`，`guard_in/guard_out` 节点先以透传占位（contracts/orchestration.md §2）
- [X] T007 实现节点骨架于 `GSagent/core/orchestration/nodes.py`：`agent`（调 LLM + 流式 delta 缓冲）、`tools`（ThreadPoolExecutor 并行执行，非暂停路径）、`compact`（复用 SessionCompactor/ContextWindowLimiter）、`should_continue`；事件先入 `_events`/`_stream_messages` 缓冲（contracts/orchestration.md §2 规则 1）
- [X] T008 改造 `GSagent/core/agents/tool_calling_llm.py`：构造时注入 `graph.py` 编译图，`call_stream()` 内部遍历 `graph.stream()` 把缓冲映射为 `StreamMessage` 逐个 yield，`run_task()` 改 `graph.invoke()`，签名与暂停/取消参数语义不变（contracts/orchestration.md §1，FR-003/011）
- [X] T009 [P] 回归门禁测试于 `tests/unit/orchestration/test_call_stream_contract.py`：用 ScriptedLLM 打桩，断言同一输入下非暂停路径的 `StreamMessage` 序列与重构前一致（quickstart S1，SC-007）
- [X] T010 实现阶段首日验证 V-01（`opentelemetry-instrumentation-litellm` 与当前 litellm 1.x 兼容性；受阻则按 research.md R-01 回退为 LiteLLMProvider 内手动 span）与 V-02（`update_state` + 重启图在 thread_id 下恢复行为），结论记录回 `research.md`

**Checkpoint**: 图骨架完成，非暂停路径回归通过——用户故事可开始并行

---

## Phase 3: User Story 1 - 运行全程可观测、可追溯（Priority: P1）🎯 MVP

**Goal**: 任务→步骤→LLM→工具完整链路可导出，token/费用可聚合，内容默认脱敏，后端厂商中立

**Independent Test**: 开启 OTel 运行一次含工具任务后，可在可观测后端（或 in-memory exporter）看到完整链路与 token/费用（quickstart S2/S3/S4）

### Tests for User Story 1（Test-First）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T011 [P] [US1] OTel 事件测试于 `tests/unit/observability/test_otel_events.py`：in-memory span exporter 断言任务/LLM/工具 span 存在、共享 trace_id、LLM span 带 `gen_ai.usage.*` 与 `llm.cost.usd`（SC-001/002）

### Implementation for User Story 1

- [X] T012 [P] [US1] 在 `GSagent/core/observability/telemetry.py` 完成自动埋点启用（`LangchainInstrumentor` + litellm instrumentor）+ 手动业务 span 装饰器工厂 `traced_node(name)`（`node.*`、`route.decision`、`node.output_keys` 属性，contracts/observability.md §2）
- [X] T013 [US1] 在 `GSagent/core/orchestration/nodes.py` 各节点接入事件构造：`agent` 节点发射 `LLM_REQUEST/RESPONSE`，`tools` 节点发射 `TOOL_DECISION/CALL_START/CALL_END/ERROR`，外层任务 span 发射 `AGENT_START/END`；`AgentEventEnvelope.trace_id/span_id/parent_span_id` 取自 `get_current_span().get_span_context()`，未接 OTel 留空（FR-001，data-model.md §3，R-04）
- [X] T014 [US1] 实现 LLM 调用费用记录：复用 `CostEstimator` 在 LLM span 写 `llm.cost.usd`（无定价条目不写），`AgentEventEnvelope.tokens_in/out/cost_usd` 同步回填（FR-002）
- [X] T015 [US1] 在 `GSagent/core/observability/emitter.py` 接线 `EventEmitter` 到图节点（事件写 `MemoryEventStore`），`MetricsAggregator` 从事件流聚合 `AgentMetrics`/`TaskMetrics`（frozen 快照唯一来源，FR-008）
- [X] T016 [US1] 隐私开关联动：`capture_full_content` 默认 False，`trace_content=true` 且显式开启才写 `gen_ai.prompt/completion`；`AgentEventEnvelope.payload` 一律经 `payload_redacted()` 脱敏（FR-007，contracts/observability.md §3）

**Checkpoint**: US1 独立可测——开启 OTel 运行即可见完整链路与指标，关闭 OTel 零影响

---

## Phase 4: User Story 2 - 显式编排：流程可控、可中断、可恢复（Priority: P2）

**Goal**: 审批/前端暂停可恢复、步数熔断、死循环检测、兄弟调用同批结算——编排图完整化

**Independent Test**: 触发审批暂停→下次传 `tool_decisions` 接续→完成且不重复执行（quickstart S6/S7）

### Tests for User Story 2（Test-First）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T017 [P] [US2] 暂停/恢复测试于 `tests/unit/orchestration/test_pause_resume.py`：ScriptedLLM 触发 `APPROVAL_REQUIRED`→下次 `call_stream(tool_decisions=...)` 恢复→断言不重复执行已完成节点（SC-005）；前端暂停 `FRONTEND_PAUSE`→`frontend_tool_results` 接续同测
- [X] T018 [P] [US2] 熔断测试于 `tests/unit/orchestration/test_step_limits.py`：达到 `max_steps`（默认 20）返回部分结果并带 `max_steps_reached=True`；连续 3 步相同工具调用触发死循环终止（FR-005，宪法 2.3）

### Implementation for User Story 2

- [X] T019 [US2] 审批暂停实现：`tools` 节点遇 `APPROVAL_REQUIRED` 设 `pause="approval"`，图提前返回；下次 `call_stream` 收到 `tool_decisions` 经 `update_state` 写入决策重启图继续（R-03，FR-004）
- [X] T020 [US2] 前端暂停实现：`FRONTEND_PAUSE` 设 `pause="frontend"`，`frontend_tool_results` 经 `update_state` 接续（FR-004）
- [X] T021 [US2] 步数熔断 + 死循环检测：`iteration >= max_steps` 走 `guard_out`/END 并标 `terminated="max_steps"`；`last_tool_calls` 连续 3 步相同 → 终止（FR-005，宪法 10.1）
- [X] T022 [US2] 兄弟调用同批结算：`tools` 节点并行批中任一调用触发暂停时，同批其余结果照常写入 messages（防 tool_call_id 孤儿），暂停事件在批结算后产出（contracts/orchestration.md §2 规则 2，对齐既有 tool_calling_llm.py L336 语义）

**Checkpoint**: US1 与 US2 独立可测——编排图完整支持暂停/恢复/熔断，且链路完整

---

## Phase 5: User Story 3 - 全链路安全护栏与审计留痕（Priority: P3）

**Goal**: 输入侧拦截注入/违规、输出侧结构校验与敏感过滤，全部审计留痕、敏感脱敏

**Independent Test**: 注入样本被 `guard_in` 拦截且审计 `blocked`；结构化输出校验失败触发 Fallback 且留痕（quickstart S5）

### Tests for User Story 3（Test-First）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T023 [P] [US3] Guardrail 测试于 `tests/unit/policy/test_guardrails.py`：注入样本拦截率 100% 且 `AuditLog` 出现 `guardrail_input/blocked`；结构化输出 schema 校验失败走 Fallback 不抛错；审计 JSONL 敏感键明文出现率 0（SC-006）

### Implementation for User Story 3

- [X] T024 [P] [US3] 实现输入侧 `GSagent/core/policy/input_guard.py`：`InputGuard.check(text) -> GuardResult`（注入模式 + 违禁词内置默认集 + `guardrails.input.deny_patterns` 追加），拦截返回 `allowed=False`（R-05，FR-006）
- [X] T025 [P] [US3] 实现输出侧 `GSagent/core/policy/output_guard.py`：`OutputGuard.check(content, expected_schema) -> OutputCheckResult`，仅对结构化输出校验 schema，分层 Fallback（轻量修复→定向重试≤2→降级），敏感过滤复用 `payload_redacted`（R-05，FR-006，宪法 5.4）
- [X] T026 [US3] 在 `GSagent/core/orchestration/graph.py`/`nodes.py` 挂载 `guard_in`（编排入口，拦截中止本轮）与 `guard_out`（最终答案出口），受 `guardrails.input.enabled`/`output.enabled` 开关控制（FR-006）
- [X] T027 [US3] 审计留痕接线：`guardrail_input`/`guardrail_output` 事件经 `AuditLog.record`（outcome=blocked/fallback/ok，payload 不含原始敏感全文），复用 `AuditUsageMixin._session_id` 同源 session（FR-006/007，contracts/guardrails.md §2/3）

**Checkpoint**: 三个用户故事均独立可测——Guardrail 与既有 PathGuard/CommandGuard/HITL 正交共存

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 文档、全量验证、清理与性能确认

- [X] T028 [P] 更新项目文档：`CLAUDE.md`（架构表格补 orchestration/telemetry/Guardrail 模块）、`README.md`（功能列表）、`mkdocs.yml` nav 注册新增 `docs/observability.md`，并 `grep -rn` 更新旧 URL/锚点引用（CLAUDE.md URL 变更规范）
- [X] T029 运行 `specs/001-langgraph-otel-refactor/quickstart.md` 全部场景 S1~S7 验证并修复失败项（含 Jaeger 链路人工确认 S2/S3）
- [X] T030 清理：确认 `GSagent/core/agents/tool_calling_llm.py` 旧手写循环死代码无引用后移除遗留逻辑（保留外壳接口），检查 `main_agent.py`/`runtime/server.py`/`plan/executor.py` 消费点兼容
- [X] T031 性能与依赖验证：对照重构前后 P95 端到端耗时增幅 ≤20%（SC-003）；`uv.lock` 无冲突依赖；`opentelemetry` 未启用路径零 OTel 导入开销（FR-010）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup（Phase 1）**: 无依赖，可立即开始
- **Foundational（Phase 2）**: 依赖 Setup —— **阻塞所有用户故事**（图骨架是 US1/US3 挂载点、US2 扩展基础）
- **User Stories（Phase 3+）**: 全部依赖 Foundational 完成
  - US1（P1）与 US2（P2）存在部分重叠面：US1 的 T013 事件接线需 T007 节点骨架，US2 的 T019~T022 是 T007 的扩展——建议 US1 先行（MVP），US2 紧随，二者共享图骨架可串行推进
  - US3（P3）依赖 Foundational 的 `guard_in/guard_out` 占位节点（T006），可在 US1/US2 完成后并行实现
- **Polish（Final Phase）**: 依赖全部用户故事完成

### User Story Dependencies

- **User Story 1（P1）**: Foundational 图骨架后即可开始——无对其他故事的依赖，**MVP 交付**
- **User Story 2（P2）**: Foundational 后即可开始——与 US1 共享图节点文件（nodes.py），按 US1→US2 顺序避免同文件冲突
- **User Story 3（P3）**: Foundational 的占位节点后即可开始——实现文件（policy/）与 US1/US2 正交，可完全并行

### Within Each User Story

- 测试先写并 FAIL → 再实现
- 模型/状态（state.py）→ 图/节点（graph.py/nodes.py）→ 接线（emitter/config）→ 集成验证
- 核心实现先于集成；故事完成后进入下一优先级

### Parallel Opportunities

- Setup 中 T002/T003 可并行（不同文件）
- Foundational 中 T005 与 T004/T006/T007 可并行（telemetry 独立于图）
- US1 中 T011（测试先行）、T012（埋点装饰器）与 T013~T016 按序；T012 与 T016 可并行
- US3 中 T024/T025（两个 Guardrail 文件正交）可并行，T023 测试先行
- US3 与 US1/US2 实现可完全并行（policy/ 与 orchestration/ 不同目录）

---

## Parallel Example: User Story 1

```bash
# 测试先行（US1）：
Task: "OTel 事件测试在 tests/unit/observability/test_otel_events.py"
# 埋点装饰器与隐私开关并行：
Task: "手动业务 span 装饰器在 GSagent/core/observability/telemetry.py"
Task: "隐私开关联动在 GSagent/core/observability/models.py（capture_full_content/payload_redacted）"
```

---

## Implementation Strategy

### MVP First（仅 User Story 1）

1. 完成 Phase 1: Setup（依赖 + 配置 + 包结构）
2. 完成 Phase 2: Foundational（CRITICAL —— 图骨架 + call_stream 适配 + 回归门禁 T009）
3. 完成 Phase 3: User Story 1（可观测完整）
4. **STOP and VALIDATE**: quickstart S1（回归绿）+ S2/S3/S4（链路/隐私/降级）
5. 交付 MVP：编排图可跑 + 全链路可观测

### Incremental Delivery

1. Setup + Foundational → 图骨架回归通过
2. US1 可观测 → 独立验证（MVP）
3. US2 暂停/恢复/熔断 → 独立验证
4. US3 Guardrail → 独立验证
5. Polish 文档/清理/性能

### 关键顺序约束（同文件避免冲突）

- `nodes.py` 被 US1（T013 事件）与 US2（T019~T022）共同修改 → 建议 US1 完成后 US2 再改，或按任务顺序串行执行
- `tool_calling_llm.py` 仅 T008 一次性改造，后续 US 不动

---

## Notes

- [P] 任务 = 不同文件、无依赖
- [Story] 标签映射到 spec.md 用户故事，可追溯
- **首个实现步骤**：先跑 T010 验证 V-01/V-02（instrumentor 兼容 + checkpointer 恢复），结论可能影响 T005/T019 的具体实现
- 验证项/回退方案见 `research.md`（V-01/V-02）
- 测试遵守 CLAUDE.md：离线用 ScriptedLLM 打桩，LLM 相关打 `llm` marker
- 提交遵循 CLAUDE.md Git 工作流：`git commit -s --no-verify`，只新增 commit 不 amend
- 每个 checkpoint 停止并独立验证对应用户故事
