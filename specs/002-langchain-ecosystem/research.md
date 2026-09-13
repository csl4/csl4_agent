# Research: 完全迁移到 langchain/langgraph 生态

> 阶段：Phase 0（/speckit-plan）｜ 日期：2026-09-13
> 对应 spec：`spec.md`（FR-001~FR-012）
> 方法：本地依赖树核实 + langchain_core 1.6.2 / langgraph 1.2.11 API 实测 + 既有代码结构分析。

**已实测确认的 API**（2026-09-13）：`@tool`/`BaseTool`/`StructuredTool`（`langchain_core.tools`）、`ToolNode`（`langgraph.prebuilt`，支持 `handle_tool_errors`/`messages_key`/`wrap_tool_call`）、`BaseMessage` 家族 + `add_messages`（`langgraph.graph.message`）、`AIMessage.tool_calls`/`invalid_tool_calls`。

---

## R-01 决策：消息层 BaseMessage 转换

**Decision**: 编排图消息状态改为 `Annotated[list[BaseMessage], add_messages]`；新增 `GSagent/core/llm_adapter.py` 提供 `dict_to_messages()` / `messages_to_dict()`（OpenAI dict ↔ BaseMessage 双向转换），作为**唯一转换点**供既有消费方（StreamMessage 事件、审计、截断、prompts 构建器）适配。

**Rationale**:
- `add_messages` 原生追加式合并（Human/AI/Tool 消息自动配对 tool_call_id），替代手写覆盖/reducer。
- `AIMessage.tool_calls` 是生态标准结构（name/args/id），下游（工具执行、事件、展示）统一读取。
- 既有消费方（`build_chat_messages`、`SessionCompactor`、审计、事件）主要按 dict 结构工作——通过 `messages_to_dict()` 适配，避免全链路重写（宪法 V：最小改动面）。

**Alternatives considered**:
1. **全链路重写为 BaseMessage**（审计/截断/事件全部改）：原生但改动面巨大（001 已适配 dict 的 105 测试全要重写）——否决，用转换点隔离。
2. **保留 dict + langchain 适配**：不满足用户「消息用 BaseMessage」要求——否决。

---

## R-02 决策：LLM 层 ChatOpenAI 装配

**Decision**: 用 `langchain_openai.ChatOpenAI` 替换 `LiteLLMProvider`；新增 `GSagent/core/providers/factory.py` 的 `create_chat_model(config)`：
- `model` ← `llm.model`、`api_key` ← `llm.api_key`、`base_url` ← `llm.base_url`（OpenAI 兼容网关，deepseek 等）。
- 工具绑定：`create_chat_model(config, tools=None)` 内 `bind_tools(tools)` 产出带 tool calling 的模型。
- usage 提取：`AIMessage.response_metadata["token_usage"]`（`prompt_tokens`/`completion_tokens`/`total_tokens`）→ 既有 `ContextWindowUsage`，供审计/事件/成本。

**Rationale**:
- 用户确认 ChatOpenAI。deepseek 提供 OpenAI 兼容 `/chat/completions`，`base_url` 直连可行。
- 原生 `tool_calls` 与 `token_usage` 自动可用，001 的 OTel 自动埋点（langchain instrumentor）对原生模型类覆盖更完整（宪法 13.1 增强）。
- `count_tokens`/`context_window`：`ChatOpenAI.get_num_tokens_from_messages()` / 模型上下文窗口配置。

**Alternatives considered**:
1. **ChatLiteLLM**（langchain-community）：保留 litellm 多网关，但用户未选且引入大依赖——否决。
2. **保留 LiteLLMProvider 适配 langchain**：非原生，不满足用户——否决。

> ⚠️ **实现验证项 V-01**：`langchain-openai` 安装后，deepseek base_url 的 tool calling 兼容性实测（OpenAI 兼容端点）。

---

## R-03 决策：工具层 @tool 包装守卫/审批

**Decision**: 全部工具集重写为 `@tool` 装饰器函数；新增 `GSagent/core/tools/registry.py` 提供：
- `register_tool(fn)` 注册表（替代 `BUILTIN_PYTHON_TOOLSETS`）。
- `wrap_with_guards(tool_fn, guards)` 包装层：调用前执行 `PathGuard`/`CommandGuard`（`rules.guard_kind_for` 分类），拦截返回错误 ToolMessage + 审计 `blocked`。
- `wrap_with_approval(tool_fn, hitl, loop)` 审批包装层：高风险工具返回「审批占位」信号，编排层 `interrupt()` 暂停（保持现有 HITL 语义）。
- `build_tool_node(tools, loop)` → `ToolNode`（`handle_tool_errors` 兜底）。

**Rationale**:
- `@tool` 无 `ToolInvokeContext` 参数——守卫/审批/用户上下文通过**闭包捕获**注入（`wrap_with_guards(tool_fn, guards)`），不依赖 `@tool` 直接支持（spec Edge Cases）。
- `ToolNode` 自动把 `AIMessage.tool_calls` 分发到对应工具、产出 `ToolMessage`，替代手写并行执行。
- 审批：包装层对需审批工具返回 `ApprovalRequirement` 信号 → 编排 tools 节点收集 → `interrupt()` 暂停 → 恢复后 `Command(resume)` 决策 → 重新执行/拒绝（001 已验证该模式）。

**Alternatives considered**:
1. **保留 Toolset/Tool 接入 langgraph**：不满足用户「重写为 @tool」——否决。
2. **@tool 装饰器内嵌守卫逻辑**（每工具手写）：重复且易漏——否决，用统一包装层。

---

## R-04 决策：编排节点适配（agent bind_tools / tools ToolNode）

**Decision**: 保留 guard_in/agent/tools/guard_out 节点结构，内部 langchain 化：
- **agent 节点**：`model = create_chat_model(config, tools).bind_tools(tools)` → `model.invoke(messages)` 得 `AIMessage`；流式用 `astream`/`stream` 缓冲 delta；usage 提取进事件/审计。
- **tools 节点**：`build_tool_node(tools, loop)` 的 `ToolNode` 执行（`tool_node.invoke(state)`），或保留自定义并行 + interrupt 审批（若需同批暂停结算语义）。**倾向自定义节点 + ToolNode 内部执行**：既有「同批兄弟调用全结算」语义（防 tool_call_id 孤儿）由自定义层保持。
- **should_continue**：`last_msg = state["messages"][-1]`，`getattr(last_msg, "tool_calls", None)` 判定。
- **guard_in/guard_out**：输入/输出校验不变，适配 BaseMessage（取最新 `HumanMessage`/`AIMessage` content）。

**Rationale**: 用户要求保留自定义节点结构。节点内部用 langchain 标准对象，既满足「生态原生」又保留现有拓扑与 interrupt 暂停恢复（FR-003/010）。

> ⚠️ **实现验证项 V-02**：`ToolNode` 与现有「审批同批结算」语义的兼容——若 `ToolNode` 天然支持（工具返回审批信号），用 prebuilt；否则自定义 tools 节点内部复用 `ToolNode` 的执行逻辑。

---

## R-05 决策：审计/事件适配 BaseMessage

**Decision**: 审计（`AuditUsageMixin`）与事件流（`AgentEventEnvelope`）**不重构**，通过 `llm_adapter` 转换层适配：
- `_audit_model_call`：从 `AIMessage.response_metadata["token_usage"]` 提取 token → 既有 `ContextWindowUsage` → 审计/事件。
- 事件发射（`LLM_RESPONSE`）：`tokens_in/out` 从 token_usage 映射；`cost_usd` 复用 `CostEstimator`。
- `StreamMessage`（SSE 事件）data 中的消息快照：经 `messages_to_dict()` 输出（前端不变）。

**Rationale**: 外部契约（StreamMessage/审计/事件流）是 CLI/serve/多 Agent 消费方依赖的稳定边界（spec FR-007/008）——保持输出形态，仅内部源从 dict 换 BaseMessage，经转换层隔离。

---

## R-06 决策：测试打桩 FakeChatLLM

**Decision**: `tests/helpers.py` 新增 `FakeChatLLM(BaseChatModel)`：实现 `_generate`（返回预设 `ChatResult`/`AIMessage`，含 `tool_calls`）与 `_stream`，离线打桩 langchain 模型调用（不走 HTTP）。既有 `ScriptedLLM`（dict LLM）随 `LiteLLMProvider` 移除。

**Rationale**: langchain 生态的标准打桩方式（自定义 `BaseChatModel`），宪法 IV「离线测试零真实 API」。`FakeChatLLM` 与 `@tool`/`ToolNode`/`add_messages` 全链路可测。

---

## R-07 决策：外部契约保持与回归门禁

**Decision**: 保留 `StreamMessage`/CLI/配置/审计/事件流对外形态；迁移设**回归门禁**：既有 105 测试全适配（dict→BaseMessage 断言）后全绿，外加迁移新增测试（工具等价、守卫/审批行为、事件序列一致）。

**Rationale**: 宪法 14.1「变更可回滚」+ spec SC-003/004——外部契约是回归锚点，任何迁移步骤不得破坏。

---

## 决策汇总

| # | 决策 | 落实 FR |
|---|---|---|
| R-01 | BaseMessage + add_messages；`llm_adapter` 转换点隔离 | FR-001/003/008 |
| R-02 | ChatOpenAI（base_url 指 OpenAI 兼容网关）+ `create_chat_model` 工厂 | FR-002/011 |
| R-03 | 全部工具 `@tool` + 守卫/审批包装层 + ToolNode | FR-004/005/006/012 |
| R-04 | 保留节点结构，agent 用 bind_tools，tools 用 ToolNode + interrupt | FR-003/010 |
| R-05 | 审计/事件经转换层适配，外部形态不变 | FR-007/008 |
| R-06 | FakeChatLLM（BaseChatModel）离线打桩 | SC-001/006 |
| R-07 | 外部契约回归门禁（105 测试全适配 + 迁移测试） | SC-003/004 |

**验证项**：V-01（deepseek base_url tool calling 兼容）、V-02（ToolNode 与审批同批结算语义兼容）。

### 验证结论（2026-09-13，实现阶段 T004）

- **V-01 deepseek tool calling 兼容**：⚠️ **未能实测**。`langchain-openai` 因 base_llm site-packages 只读安装被拒（`os error 5`，需管理员授权）。代码已 try-import + FakeChatLLM 离线覆盖（`FakeChatLLM.tool_calls` 结构验证通过）；待授权安装后按 quickstart S4 实测。
- **V-02 ToolNode 行为**：✅ **已实测**。在最小 StateGraph 中，`ToolNode` 从 `AIMessage.tool_calls` 正确分发到 `@tool`、产出 `ToolMessage` 且 `add_messages` 按 `tool_call_id` 配对；工具缺失时 `handle_tool_errors` 兜底。**结论**：`ToolNode` 可直接作编排 tools 节点核心；审批「同批兄弟调用全结算」由工具包装层 + 编排 `interrupt()` 承担（不依赖 ToolNode 内部，001 已验证模式）。
