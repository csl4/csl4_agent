# Implementation Plan: LangGraph 编排 + OpenTelemetry 可观测重构（宪法对齐）

**Branch**: `001-langgraph-otel-refactor` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-langgraph-otel-refactor/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

按 GSDOC 宪法与 OTel/LangGraph 操作文档重构项目，聚焦四项核心差距：
1. **LangGraph 显式编排**：用 `StateGraph` 重写 792 行手写主循环，外部 `ToolCallingLLM.call_stream()` → `Generator[StreamMessage]` 契约不变（CLI/serve/多 Agent/Plan 模式零改动）。
2. **OTel 可观测接入**：OpenLLMetry 自动埋点（`opentelemetry-instrumentation-langchain` + `-litellm`）+ 手动业务 span 叠加；GenAI 语义约定；token/费用/链路三层贯穿。
3. **Guardrail 补齐**：新增输入侧（注入/内容合规）与输出侧（结构校验/敏感过滤）Guardrail 入 `policy/`，复用 AuditLog 留痕。
4. **事件流接线**：`EventEmitter` 注入图节点，`AgentEventEnvelope.trace/span_id` 取自 OTel 当前 span context，指标快照真实随执行生成。

技术方案详见 [research.md](research.md)（R-01~R-06，含 2 项实现阶段验证项）。

## Technical Context

**Language/Version**: Python 3.10+（pyproject `requires-python >= 3.10`）

**Primary Dependencies**:
- 现有（不变）：`typer` / `pydantic>=2` / `litellm>=1.0` / `tenacity` / `langgraph>=1.2.11` / `langchain-core`（传递）
- 新增：`opentelemetry-instrumentation-langchain>=0.55`、`opentelemetry-instrumentation-litellm>=0.55`
- 可选新增（serve/HTTP 链路）：`opentelemetry-instrumentation-fastapi`、`opentelemetry-instrumentation-httpx`
- 复用（已在依赖树）：`opentelemetry-sdk` / `opentelemetry-api` / `exporter-otlp-proto-http`（1.42.1，由 `langchain-cli`/`langsmith[otel]` 传递）

**Storage**: SQLite（长期记忆 `memory.db` / 任务队列 `runtime.db` / 会话记忆，均既有）+ JSONL 审计（既有）+ 内存事件仓库 `MemoryEventStore`（既有，接线）——本次不新增存储。

**Testing**: pytest + `ScriptedLLM` 打桩（离线，不走 HTTP）+ `pytest-xdist` 并行；LLM 相关打 `llm` marker。新图节点用 ScriptedLLM 验证调度，OTel 用 `in-memory` span exporter 断言 span 属性。

**Target Platform**: 跨终端 CLI（Windows PowerShell / bash / zsh）+ FastAPI serve（既有 `agent serve`）

**Project Type**: CLI 工具 + Web 服务（混合，单机部署）

**Performance Goals**: P95 端到端耗时增幅 ≤20%（SC-003）；千级事件过滤查询 <500ms（既有 MemoryEventStore 已满足）；OTel 埋点开销异步批量导出（BatchSpanProcessor）。

**Constraints**: ①外部契约零破坏：`call_stream`/`run_task`/`set_hitl_mode`/`create_tool_calling_llm` 签名不变；②OTel 后端不可达不阻塞主流程（FR-010，try-import + 开关 + 无后端静默降级）；③`capture_full_content` 默认关，敏感内容不落盘（FR-007）；④审计/事件脱敏沿用 `payload_redacted`。

**Scale/Scope**: 单机 CLI + serve；多 Agent ≤4 SubAgent；单任务步数 ≤20（max_steps）；事件规模千级。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### 前置评估（Phase 0 前）

| 宪法条款 | 检查 | 结论 |
|---|---|---|
| 2.1 分层解耦，核心不感知细节 | 本次保留 LLM 抽象/工具插件/prompt 模块，新增编排层不破坏分层 | ✅ 满足 |
| 2.3 Agentic Loop 最大步数熔断 | 现有 `max_steps=20`，LangGraph 图保留熔断节点 | ✅ 满足 |
| 3.2 生产编排用显式图 | **本次核心**：StateGraph 显式节点+条件边重写手写循环 | ✅ 落实 |
| 第三条 一切可观测，没有 Trace 不上线 | **本次核心**：OTel 三层 Trace + GenAI 语义约定 | ✅ 落实 |
| 5.1 Prompt 是代码（模块化/版本化） | 现有 `prompts/` 构建器保留，不入本次改动 | ✅ 不破坏 |
| 5.4 校验失败分层 Fallback | 输出侧 Guardrail 采用轻量修复→定向重试→降级人工 | ✅ 落实 |
| 11.1 三道 Guardrail | 输入/输出侧新增 + 结果侧既有，全部审计留痕 | ✅ 落实 |
| 13.1 OTel GenAI 语义约定，trace_id 贯穿 | **本次核心**：envelope trace/span_id 取自 OTel context | ✅ 落实 |
| 13.3 Mock LLMBackend 单元测试零真实 API | 既有 ScriptedLLM 打桩，新增图测试沿用 | ✅ 满足 |
| 14.1 变更流程（Shadow/灰度/回归） | 本次以「外部契约不变 + 离线回归 + 单变量对比」落实 | ✅ 满足 |

**GATE 结论**：全部通过，无违规。本次重构即为了落实宪法 3.2/第三条/11.1/13.1 而存在，不产生需要 Complexity Tracking justify 的偏差。

### 后设计复评（Phase 1 后）

| 复核项 | 结果 |
|---|---|
| 编排层（orchestration/）是否保持 LLM/工具/prompt 解耦？ | ✅ 编排层只依赖既有抽象（LLM、ToolExecutor、prompts），未绕过 |
| OTel 是否成为主流程硬依赖？ | ✅ 未接入/后端不可达时 envelope 留空、主流程照跑（FR-010） |
| 事件流是否仍是指标唯一来源？ | ✅ 指标仍由 `MetricsAggregator` 从事件流聚合，frozen 快照不变 |
| Guardrail 是否全部留痕且脱敏？ | ✅ 复用 AuditLog + `payload_redacted` |

## Project Structure

### Documentation (this feature)

```text
specs/001-langgraph-otel-refactor/
├── plan.md              # 本文件（/speckit-plan 输出）
├── research.md          # Phase 0 技术选型（R-01~R-06 + V-01/V-02）
├── data-model.md        # Phase 1 数据模型（LangGraph state + 配置契约 + 事件映射）
├── quickstart.md        # Phase 1 验证指南
├── contracts/           # Phase 1 接口契约
│   ├── orchestration.md # 编排层契约（图节点/状态/事件输出）
│   ├── observability.md # 可观测契约（telemetry 配置 + 事件接线 + span 属性）
│   └── guardrails.md    # Guardrail 契约（输入/输出侧拦截语义 + 审计事件）
└── tasks.md             # Phase 2 输出（/speckit-tasks，本命令不创建）
```

### Source Code (repository root)

```text
GSagent/
├── core/
│   ├── agents/
│   │   ├── tool_calling_llm.py      # [改造] 内部引擎换 LangGraph 图，外部接口不变
│   │   └── audit_mixin.py           # [不变] 审计/用量埋点 mixin（图节点复用）
│   ├── orchestration/               # [新增] LangGraph 编排层（宪法 2.1 分层）
│   │   ├── __init__.py
│   │   ├── state.py                 # GraphState：消息/工具/暂停/事件缓冲字段
│   │   ├── graph.py                 # StateGraph 组装 + compile(checkpointer)
│   │   └── nodes.py                 # 节点：agent(LLM) / tools(并行执行) / compact /
│   │                                #        guard_in / guard_out / should_continue
│   ├── observability/
│   │   ├── telemetry.py             # [新增] OTel 初始化（方案 A：自动 + 手动双层）
│   │   ├── emitter.py               # [接线] EventEmitter 注入图节点，trace_id 回填
│   │   └── models.py                # [不变] 事件/指标/状态模型（已落地）
│   └── policy/
│       ├── input_guard.py           # [新增] 输入侧 Guardrail（注入/内容合规）
│       ├── output_guard.py          # [新增] 输出侧 Guardrail（结构校验/敏感过滤）
│       └── audit.py                 # [不变] 审计 JSONL（复用留痕）
└── config.py                        # [改造] 新增 observability/guardrail 配置段装配
```

**Structure Decision**: 新增 `GSagent/core/orchestration/` 承载 LangGraph 图（宪法 2.1 五层中的 Orchestrator 层），与既有 `agents/`（角色）、`observability/`（可观测）、`policy/`（安全）解耦；`tool_calling_llm.py` 只保留薄外壳（构造注入图 + 外部契约映射），编排细节下沉到 orchestration/。测试 `tests/` 镜像此结构（当前 tests/ 迁移中，恢复后按 `tests/unit/orchestration/`、`tests/unit/policy/`、`tests/unit/observability/` 补齐）。

## Complexity Tracking

> 无宪法违规，无需 justify。本次所有复杂度（编排层、双 instrumentor、Guardrail 新增）均为宪法条款的直接落实，非架构偏差。
