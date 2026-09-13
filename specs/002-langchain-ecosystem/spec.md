# Feature Specification: 完全迁移到 langchain/langgraph 生态

**Feature Branch**: `002-langchain-ecosystem`

**Created**: 2026-09-13

**Status**: Draft

**Input**: User description: "完全导入 langgraph 的包，把我定义的工具/agent 结构迁移到 langchain 生态"

> 用户决策（四项）：①工具重写为 langchain `@tool` 装饰器 + `ToolNode`；②Agent 保留自定义 StateGraph 节点结构；③LLM 换 `langchain-openai`/`ChatLiteLLM` 等 langgraph 原生模型类；④消息用 langchain `BaseMessage` + `add_messages`。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 消息与编排 langchain 化（Priority: P1）

作为 Agent 开发者，我需要 Agent 主循环内部完全用 langchain 生态：消息是 `BaseMessage`（Human/AI/Tool/System），状态用 `add_messages` reducer 合并，LLM 是 langgraph 原生模型类（自动产出 `AIMessage.tool_calls`、`ToolMessage`）。自定义节点结构保留，但节点间流转的是 langchain 对象，而非 OpenAI dict。

**Why this priority**: 这是后续工具/审计/可观测迁移的基础——消息格式决定一切下游适配。不做这一步，其他 langchain 化无从谈起。

**Independent Test**: 一条消息流完整跑通：用户输入 → `BaseMessage` 列表 → 原生 LLM → 工具调用（`AIMessage.tool_calls`）→ `ToolMessage` → 最终回答；`add_messages` 正确合并多轮消息。可独立验证。

**Acceptance Scenarios**:

1. **Given** 用户输入，**When** 进入主循环，**Then** 消息以 `BaseMessage` 列表存在于图状态，`add_messages` 追加式合并。
2. **Given** LLM 返回工具调用，**When** 解析响应，**Then** 得到 `AIMessage.tool_calls` 结构，工具结果以 `ToolMessage` 回填。
3. **Given** 一轮多步工具交互，**When** 检查状态，**Then** 消息按序追加、无重复、工具调用与其结果正确配对。
4. **Given** 暂停恢复（审批），**When** 下次接续，**Then** 基于 `BaseMessage` 状态从断点继续，不丢失上下文。

---

### User Story 2 - 工具层 langchain `@tool` 化（Priority: P1）

作为 Agent 使用者，我需要现有全部工具集（bash/文件/记忆/沙箱/技能等）以 langchain `@tool` 装饰器定义、由 prebuilt `ToolNode` 执行，且原有安全能力**不变**：命令/路径守卫仍拦截、高风险写操作仍触发审批、执行留痕仍在审计。

**Why this priority**: 用户明确要求工具层重写为 langchain 风格。工具是 Agent 的能力核心，且必须保住既有安全护栏。

**Independent Test**: 一个典型工具（如 bash 执行）迁移后：`@tool` 定义 → 注册进 `ToolNode` → 编排中正常调用；危险命令仍被 `CommandGuard` 拦截并审计留痕。可独立验证。

**Acceptance Scenarios**:

1. **Given** 现有任一工具集，**When** 迁移完成，**Then** 以 `@tool` 装饰器定义、可注册进 `ToolNode`，行为与迁移前等价。
2. **Given** 破坏性命令/非法路径，**When** 工具执行，**Then** 仍被守卫拦截（行为不变）。
3. **Given** 需审批的高风险工具，**When** 调用，**Then** 仍触发审批暂停/确认。
4. **Given** 工具执行，**When** 结束，**Then** 审计留痕与事件流（可观测）照常产生。

---

### User Story 3 - LLM 与可观测生态迁移（Priority: P2）

作为使用者，我需要 LLM 调用走 langgraph 原生模型类（OpenAI 兼容网关或 LiteLLM 适配），token 用量/费用/链路可观测仍完整；既有的 StreamMessage SSE 事件流、审计、事件流（AgentEventEnvelope）对外契约不变。

**Why this priority**: LLM 层决定成本/用量可观测与多模型兼容；外部契约（SSE 事件流/CLI/审计）是 CLI、serve、多 Agent 消费方依赖的稳定边界。

**Independent Test**: 换原生模型类后，跑一条含工具任务：token/费用在事件流正确、链路完整；CLI 输出的事件序列与迁移前一致。可独立验证。

**Acceptance Scenarios**:

1. **Given** 配置的模型，**When** 创建 LLM，**Then** 使用 langgraph 原生模型类（OpenAI 兼容 base_url 或 LiteLLM 适配）。
2. **Given** 一次 LLM 调用，**When** 完成，**Then** token 用量/费用进入事件流与审计，数值与真实一致。
3. **Given** CLI 运行，**When** 消费事件，**Then** `StreamMessage` SSE 事件序列与迁移前一致（ANSWER_DELTA/USAGE/ANSWER_END/审批等）。
4. **Given** 审计/事件查询，**When** 检查，**Then** 敏感字段仍脱敏、链路 trace_id 仍贯穿。

---

### Edge Cases

- 现有工具依赖自定义 `ToolInvokeContext`（user_approved/request_context 等）——`@tool` 无该参数，守卫/审批上下文如何传入（包装层设计）。
- 审批暂停恢复：`BaseMessage` 状态 + `interrupt()` 的 resume 值如何携带用户决策。
- 压缩/截断：`SessionCompactor` 面向 dict 消息，需适配 `BaseMessage`。
- 孤儿 tool_call：`AIMessage.tool_calls` 无对应 `ToolMessage` 时的清理。
- 多模型网关：deepseek 走 OpenAI 兼容 base_url vs ChatLiteLLM 保留 litellm 网关。
- 工具迁移顺序：bash 等依赖子进程/环境的工具，`@tool` 化时配置注入方式。
- 既有测试（105 个）全部适配：dict 消息断言 → `BaseMessage` 断言。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 使用 langchain `BaseMessage`（Human/AI/Tool/System）作为编排图消息状态，`add_messages` reducer 追加式合并。*（US1）*
- **FR-002**: 系统 MUST 使用 langgraph 原生模型类（`langchain-openai`/`ChatLiteLLM` 等）执行 LLM 调用，原生产出 `AIMessage.tool_calls` 与 usage。*（US1/US3）*
- **FR-003**: 系统 MUST 保留自定义 StateGraph 节点结构（guard_in/agent/tools/guard_out），节点内部流转 langchain 对象。*（US1）*
- **FR-004**: 系统 MUST 将现有全部工具集重写为 langchain `@tool` 装饰器定义，注册进 prebuilt `ToolNode`。*（US2）*
- **FR-005**: 系统 MUST 在 langchain 工具执行前保持既有命令/路径守卫拦截，拦截后审计留痕。*（US2）*
- **FR-006**: 系统 MUST 在 langchain 工具调用时保持既有审批（HITL）语义（auto/always/never + 暂停恢复）。*（US2）*
- **FR-007**: 系统 MUST 保持 `StreamMessage` SSE 事件流外部契约不变（CLI/serve/多 Agent 消费方零改动）。*（US3）*
- **FR-008**: 系统 MUST 适配审计/事件流/指标到 `BaseMessage` 世界（token/费用/链路仍完整、敏感仍脱敏）。*（US3）*
- **FR-009**: 系统 MUST 保持既有 CLI 命令集（`agent chat/run/serve` 等）与配置 schema 兼容。*（US3）*
- **FR-010**: 系统 MUST 保持 `interrupt()`/checkpointer 暂停恢复能力在 `BaseMessage` 状态下可用。*（US1）*
- **FR-011**: 系统 MUST 用 `langchain-openai` 的 `ChatOpenAI` 作为 LLM 模型类，`base_url` 指向 OpenAI 兼容网关（deepseek 等），替换 `LiteLLMProvider`。*（已确认：ChatOpenAI）*
- **FR-012**: 系统 MUST 将现有**全部**工具集一次性迁移为 `@tool` 装饰器定义（bash/文件/记忆/沙箱/技能/yaml 等），不保留旧 `Toolset`/`Tool` 执行路径。*（已确认：全部一次性）*

### Key Entities *(include if feature involves data)*

- **langchain 工具**: 以 `@tool` 装饰器定义的函数，注册进 `ToolNode`；携带守卫/审批包装层。
- **BaseMessage 消息**: `HumanMessage`/`AIMessage`（含 `tool_calls`）/`ToolMessage`/`SystemMessage`，`add_messages` 合并。
- **原生 LLM 模型类**: OpenAI 兼容或 LiteLLM 适配的 langchain 模型对象，产出 tool_calls 与 usage。
- **编排图状态**: `GraphState`（`BaseMessage` 列表 + 回合配置 + 事件缓冲），节点结构保留。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 迁移后全部既有测试适配通过（105 个基线，0 回归）；新增迁移测试覆盖各层。
- **SC-002**: 工具行为等价：同一输入，迁移前后工具执行结果一致（回归对照）。
- **SC-003**: 守卫拦截率与审批触发不变：破坏性命令/危险路径拦截率 100%，高风险工具审批 100% 触发。
- **SC-004**: `StreamMessage` 事件序列与 CLI 命令行为不变（外部契约零破坏）。
- **SC-005**: token/费用/链路可观测在迁移后仍准确（与真实用量一致，误差 <5%）。
- **SC-006**: 现有测试全部改为 `BaseMessage` 断言后全绿。

## Assumptions

- **范围**：消息层、LLM 层、工具层、编排适配、外部契约保持五块；RAG/多租户/多 Agent 重构不在本次。
- **外部契约保留**：`StreamMessage` SSE 事件流、CLI 命令集、配置 schema、审计/事件流/可观测对外形态不变。
- **Agent 结构保留**：自定义 StateGraph 节点（guard_in/agent/tools/guard_out）与 interrupt/checkpointer 暂停恢复不变，仅内部对象 langchain 化。
- **工具守卫/审批**：通过包装层（langchain 工具外层包裹守卫/审批/HITL 上下文）保持，不依赖 `@tool` 直接支持。
- **多模型网关（已确认）**：用 `langchain-openai` 的 `ChatOpenAI`，`base_url` 指向 OpenAI 兼容网关（deepseek 等）；现有 `AGENT_MODEL`/`AGENT_BASE_URL`/`AGENT_API_KEY` 配置语义保留（映射到 ChatOpenAI 参数）。
- **工具范围（已确认）**：全部工具集一次性迁移为 `@tool`，旧 `Toolset`/`Tool`/`ToolExecutor` 执行路径移除。
- **依赖新增**：`langchain-openai`（ChatOpenAI）+ 现有 `langgraph`/`langchain-core`；`langgraph-checkpoint` 按需。
- **环境**：base_llm site-packages 只读，新增依赖安装需管理员授权一次（CLAUDE.md 已有说明）。
