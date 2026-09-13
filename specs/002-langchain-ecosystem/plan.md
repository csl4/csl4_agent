# Implementation Plan: 完全迁移到 langchain/langgraph 生态

**Branch**: `002-langchain-ecosystem` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-langchain-ecosystem/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

把自定义 Agent 引擎（工具/消息/LLM 三层）全面替换为 langchain 生态，Agent 编排保留自定义 StateGraph 节点结构：
1. **工具层**：全部工具集重写为 langchain `@tool` 装饰器 + prebuilt `ToolNode`；命令/路径守卫、HITL 审批经工具包装层保持。
2. **消息层**：OpenAI dict → langchain `BaseMessage`（Human/AI/Tool/System）+ `add_messages` reducer。
3. **LLM 层**：`LiteLLMProvider` → `langchain-openai` `ChatOpenAI`（base_url 指 OpenAI 兼容网关，deepseek 等）。
4. **编排层**：保留 guard_in/agent/tools/guard_out 节点 + `interrupt()`/checkpointer 暂停恢复，节点内部流转 langchain 对象。

外部契约零破坏：`StreamMessage` SSE 事件流、CLI 命令集、配置 schema、审计/可观测对外形态不变。技术方案详见 [research.md](research.md)。

## Technical Context

**Language/Version**: Python 3.10+（pyproject `requires-python >= 3.10`）

**Primary Dependencies**:
- 新增：`langchain-openai`（ChatOpenAI；OpenAI 兼容 base_url 对接 deepseek 等）
- 已有（复用）：`langgraph>=1.2.11`、`langchain-core 1.6.2`（`@tool`/`BaseMessage`/`add_messages` 已本地验证）、`langgraph.prebuilt.ToolNode`（已本地验证）
- 移除（执行路径替换）：`litellm` 直连（保留为可选项？）——`LiteLLMProvider` 移除，`litellm` 依赖随迁移评估移除或保留兼容
- 保留（外部契约/安全/可观测）：`typer`/`pydantic`/`tenacity`/`pyyaml`/`rich`/`bashlex`/`a2a-sdk`/`opentelemetry-*`（001 已接入）

**Storage**: 不变——SQLite（记忆/任务队列）、JSONL（审计/历史）、内存事件仓库（`MemoryEventStore`）

**Testing**: pytest + langchain 离线打桩。`ScriptedLLM`（自定义 dict LLM）→ **`FakeChatLLM`**（自定义 `BaseChatModel` 子类，实现 `_generate`/`_stream` 返回预设 `AIMessage`，含 `tool_calls`）。LLM 相关打 `llm` marker。

**Target Platform**: 跨终端 CLI（PowerShell/bash/zsh）+ FastAPI serve（`agent serve`）

**Project Type**: CLI 工具 + Web 服务（混合）

**Performance Goals**: 迁移后 P95 端到端增幅 ≤20%（SC-003 外部契约）；token/费用可观测仍准确（SC-005）

**Constraints**: ①外部契约零破坏：`StreamMessage` 事件流、CLI 命令、配置 schema、审计/事件流对外形态不变；②守卫（PathGuard/CommandGuard）与审批（HITL）在 langchain 工具下行为不变（SC-003）；③`interrupt()`/checkpointer 暂停恢复保留（FR-010）；④105 个既有测试全部适配（dict 断言 → `BaseMessage` 断言）后全绿（SC-001/006）

**Scale/Scope**: 全部工具集一次性迁移（bash/文件/记忆/沙箱/技能/yaml）；消息/LLM/编排/审计/可观测适配；多 Agent 与 plan 模式消费方零改动

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### 前置评估（Phase 0 前）

| 宪法条款 | 检查 | 结论 |
|---|---|---|
| 2.1 分层解耦，核心不感知细节 | 迁移后 LLM/工具由 langchain 生态提供统一抽象，核心编排仍不感知具体实现 | ✅ 满足（抽象从自研换 langchain） |
| 2.2 模块间通信用标准化消息对象 | 消息层标准化为 langchain `BaseMessage`（生态标准），优于自研 dict | ✅ 增强 |
| 3.2 生产编排用显式图 | 保留自定义 StateGraph 节点 + interrupt/checkpointer | ✅ 保持 |
| 11.1 三道 Guardrail | 输入/输出侧（Guardrail 节点）+ 结果侧（工具守卫）全部保留，仅适配工具包装层 | ✅ 保持 |
| 5.1 Prompt 是代码 | 现有 prompts 构建器适配 `BaseMessage` 输出（`SystemMessage`/`HumanMessage`） | ✅ 保持 |
| 13.1 OTel GenAI 语义约定 | 001 已接入；langchain 自动埋点对 `ChatOpenAI` 覆盖更完整（原生模型类） | ✅ 增强 |
| 14.1 变更流程（Shadow/灰度/回归） | 以「外部契约不变 + 105 测试全适配 + 单变量分层迁移」落实 | ✅ 满足 |
| 宪法 V 重试走 tenacity | langchain 调用仍可用 tenacity 包重试；工具重试由 langchain 处理 | ✅ 保持 |

**GATE 结论**：全部通过，无违规。本次迁移是生态替换（自研 → langchain 标准），强化 2.2/13.1，不削弱任何宪法约束。

### 后设计复评（Phase 1 后）

| 复核项 | 结果 |
|---|---|
| 编排层是否仍不感知工具/LLM 细节？ | ✅ 编排只依赖 langchain 标准对象（BaseMessage/BaseChatModel/BaseTool/ToolNode） |
| 守卫/审批是否行为不变？ | ✅ 经工具包装层注入（`@tool` 闭包捕获 guards/hitl），SC-003 门禁 |
| 可观测是否仍 token/费用/链路完整？ | ✅ ChatOpenAI 原生 usage + 001 事件流/OTel 接线 |
| 外部契约是否零破坏？ | ✅ StreamMessage/CLI/审计/配置 schema 回归门禁 |

## Project Structure

### Documentation (this feature)

```text
specs/002-langchain-ecosystem/
├── plan.md              # 本文件（/speckit-plan 输出）
├── research.md          # Phase 0 技术选型（R-01~R-07）
├── data-model.md        # Phase 1（BaseMessage 状态 + 工具包装层 + 配置契约）
├── quickstart.md        # Phase 1 验证指南
├── contracts/           # Phase 1
│   ├── messages.md      # BaseMessage 转换契约（dict↔BaseMessage + 事件/审计适配）
│   ├── tools.md         # @tool 工具契约（守卫/审批包装层）
│   └── llm.md           # ChatOpenAI 装配契约（配置映射）
└── tasks.md             # Phase 2 输出（/speckit-tasks）
```

### Source Code (repository root)

```text
GSagent/
├── core/
│   ├── agents/
│   │   ├── tool_calling_llm.py      # [改造] 外壳不变；节点内部 langchain 化
│   │   └── audit_mixin.py           # [适配] _audit_model_call 接 ChatOpenAI usage
│   ├── orchestration/               # [保留+适配] 节点内部用 BaseMessage/原生 LLM/ToolNode
│   │   ├── state.py                 # [适配] messages 改 Annotated[list, add_messages]
│   │   ├── graph.py                 # [保留] 节点结构不变
│   │   └── nodes.py                 # [改造] agent 用 bind_tools；tools 用 ToolNode+interrupt
│   ├── tools/
│   │   ├── registry.py              # [新增] @tool 注册表 + 守卫/审批包装层
│   │   ├── executor.py              # [移除] ToolExecutor（被 ToolNode + 包装层替代）
│   │   ├── base.py / toolset.py     # [移除] 自定义 Tool/Toolset 类（被 @tool 替代）
│   ├── providers/                   # [移除] LiteLLMProvider → ChatOpenAI 工厂
│   │   └── factory.py               # [新增] create_chat_model(config) → ChatOpenAI
│   ├── llm_adapter.py               # [新增] 转换器：dict↔BaseMessage、usage 提取
│   ├── observability/               # [保留] 事件流/OTel（001 已接入）
│   └── policy/                      # [保留] 守卫/HITL/审计（包装层复用）
├── plugins/toolsets/                # [改造] 全部工具集重写为 @tool
│   ├── bash/  filesystem/  memory/  sandbox/  skills/  yaml_loader/
│   └── __init__.py                  # [改造] BUILTIN_PYTHON_TOOLSETS → @tool 注册表
└── config.py                        # [适配] create_llm → ChatOpenAI；工具装配 → 注册表
```

**Structure Decision**: 编排层（orchestration/）保留；工具层从「Toolset/Tool + ToolExecutor」重构为「`@tool` 注册表 + 守卫/审批包装层 + `ToolNode`」；LLM 层从「自定义 ABC + LiteLLMProvider」重构为「`create_chat_model()` 工厂 → ChatOpenAI」；新增 `llm_adapter.py` 承载 BaseMessage 转换与 usage 提取（单一适配点）。测试 `tests/` 镜像此结构。

## Complexity Tracking

> 无宪法违规，无需 justify。迁移复杂度（工具重写、消息适配、105 测试更新）为 langchain 生态替换的必然成本，非架构偏差。
