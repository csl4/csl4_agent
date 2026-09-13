---

description: "任务清单：完全迁移到 langchain/langgraph 生态"

---

# Tasks: 完全迁移到 langchain/langgraph 生态

**Input**: Design documents from `/specs/002-langchain-ecosystem/`

**Prerequisites**: plan.md（已读）、spec.md（已读）、research.md、data-model.md、contracts/

**Tests**: 本项目 CLAUDE.md/宪法 IV 硬性要求「新功能需要单元测试（Test-First）」，且迁移以「105 既有测试全适配全绿」为回归门禁（R-07，SC-001/006）。故每个用户故事包含测试任务，且测试先于实现。

**Organization**: 任务按用户故事分组。关键依赖——**消息转换层（llm_adapter）是 US1/US2/US3 的共同基础**，放 Foundational 阶段（阻塞全部 US）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- 源码 `GSagent/`，测试 `tests/`（镜像结构）

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 依赖声明、离线打桩准备

- [X] T001 在 `pyproject.toml` `[project]` dependencies 增加 `langchain-openai>=0.3`（ChatOpenAI），执行 `uv pip install -e ".[dev]"` 验证（base_llm 只读需管理员授权）
- [X] T002 [P] 在 `tests/helpers.py` 新增 `FakeChatLLM(BaseChatModel)`：实现 `_generate`/`_stream` 返回预设 `AIMessage`（含 `tool_calls`）与 `response_metadata["token_usage"]`，离线打桩 langchain 模型（R-06，宪法 IV）

---

## Phase 2: Foundational（阻塞全部用户故事）—— 消息转换层 + 技术验证

**Purpose**: `llm_adapter` 转换点 + 实现阶段首日验证；未完成前任何 US 不可开始

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 实现 `GSagent/core/llm_adapter.py`：`dict_to_messages()`（OpenAI dict → BaseMessage）、`messages_to_dict()`（BaseMessage → OpenAI dict，双向字段保真含 tool_call_id/tool_calls/name）、`extract_usage(aimessage)`（`response_metadata["token_usage"]` → 既有 `ContextWindowUsage`）（R-01，contracts/messages.md §2）
- [X] T004 实现阶段首日验证 V-01（deepseek 经 ChatOpenAI base_url 的 tool calling 兼容性；若 LLM Key 不可用则记录回退为「OpenAI 兼容端点 + 单测覆盖」）与 V-02（`ToolNode` 与「审批同批兄弟调用全结算」语义的兼容，用 FakeChatLLM 离线验证），结论记录回 `research.md`

**Checkpoint**: 转换层就位，US 可开始并行

---

## Phase 3: User Story 1 - 消息与编排 langchain 化（Priority: P1）🎯 MVP

**Goal**: 图状态 `BaseMessage` + `add_messages`；节点内部流转 langchain 对象（AIMessage.tool_calls / ToolMessage）

**Independent Test**: 一条消息流跑通：Human → BaseMessage → FakeChatLLM → AIMessage(tool_calls) → ToolMessage → 最终回答；`add_messages` 正确合并（quickstart S2）

### Tests for User Story 1（Test-First）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T005 [P] [US1] 测试于 `tests/unit/test_llm_adapter.py`：`dict_to_messages`/`messages_to_dict` 双向保真（含 tool_call_id/tool_calls）；`extract_usage` 从 token_usage 提取正确（SC-005）

### Implementation for User Story 1

- [X] T006 [US1] 改 `GSagent/core/orchestration/state.py`：`messages: Annotated[list[BaseMessage], add_messages]`（替代 OpenAI dict 覆盖语义；依赖 T003 转换层）（FR-001，data-model.md §1）
- [X] T007 [US1] 适配 `GSagent/core/prompts/messages.py` 的 `build_chat_messages`：输出 `SystemMessage` + `HumanMessage`（对齐 BaseMessage 世界，FR-001）
- [X] T008 [US1] 改 `GSagent/core/orchestration/nodes.py` 的 agent 节点：`create_chat_model(config, tools).bind_tools(tools).invoke(messages)` 得 `AIMessage`，流式 delta 缓冲、usage 提取（R-04，FR-002）
- [X] T009 [US1] 改 tools 节点：`ToolNode` 执行（`AIMessage.tool_calls` → 工具 → `ToolMessage`），或保留自定义并行 + interrupt 审批（V-02 定夺）；`last_tool_calls` 从 `AIMessage.tool_calls` 提取（R-04，FR-003）
- [X] T010 [US1] 改 `should_continue` 与 guard 节点：`state["messages"][-1]` 判 `getattr(msg, "tool_calls", None)`；输入/输出校验读最新 `HumanMessage`/`AIMessage` content（FR-003）
- [X] T011 [P] [US1] 集成测试于 `tests/unit/orchestration/test_langchain_flow.py`：FakeChatLLM 跑通「Human → AIMessage(tool_calls) → ToolMessage → 回答」全流，`add_messages` 正确合并（SC-001）

**Checkpoint**: US1 独立可测——消息流 BaseMessage 化，外部事件流输出不变

---

## Phase 4: User Story 2 - 工具层 langchain @tool 化（Priority: P1）

**Goal**: 全部工具集 `@tool` 化 + 守卫/审批包装层 + ToolNode；安全行为不变

**Independent Test**: 任一工具迁移后 `get_all_tools()` 数量一致、行为等价；破坏性命令仍拦截、高风险工具仍审批（quickstart S3）

### Tests for User Story 2（Test-First）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T012 [P] [US2] 测试于 `tests/unit/tools/test_tool_registry.py`：`get_all_tools()` 工具数 = 迁移前工具数（SC-002）；守卫包装层拦截破坏性命令/非法路径率 100% + 审计 `blocked`（SC-003）

### Implementation for User Story 2

- [X] T013 [US2] 实现 `GSagent/core/tools/registry.py`：`register_tool()`/`get_all_tools()`/`get_tool()`（替代 `BUILTIN_PYTHON_TOOLSETS` 工厂；R-03，contracts/tools.md §1）
- [X] T014 [P] [US2] 实现 `wrap_with_guards(tool, guards)`：按 `rules.guard_kind_for` 分类执行 PathGuard/CommandGuard，拦截返回错误 ToolMessage + `AuditLog(blocked)`（R-03，contracts/tools.md §2）
- [X] T015 [P] [US2] 实现 `wrap_with_approval(tool, hitl, loop)`：HITL 裁决 → 审批信号 → 编排 interrupt() 暂停 → Command(resume) 恢复执行/拒绝（R-03，contracts/tools.md §3）
- [X] T016 [P] [US2] 迁移 bash 工具集为 `@tool` 于 `GSagent/plugins/toolsets/bash/lc_tools.py`（复用 execute_bash_command + validate_command 动态审批，经守卫/审批包装）（SC-002）
- [X] T017 [P] [US2] 迁移 filesystem 工具集为 `@tool` 于 `GSagent/plugins/toolsets/filesystem/`
- [X] T018 [P] [US2] 迁移 memory 工具集（remember/search_memory）为 `@tool` 于 `GSagent/plugins/toolsets/memory/`
- [X] T019 [P] [US2] 迁移 sandbox 工具集为 `@tool` 于 `GSagent/plugins/toolsets/sandbox/lc_tools.py`（复用 validate_command + execute_in_shell）
- [X] T020 [US2] 适配 YAML 工具集加载为 `@tool` 于 `GSagent/plugins/toolsets/yaml_lc_loader.py`（YAML 工具 → StructuredTool，shlex.quote 防注入）
- [X] T021 [US2] 实现 `build_tool_node(tools, loop)` 于 `GSagent/core/tools/registry.py`，接入编排 tools 节点（V-02 定夺 ToolNode vs 自定义并行）（R-03/04，contracts/tools.md §4）

**Checkpoint**: US1 与 US2 独立可测——工具全部 @tool 化且守卫/审批行为不变

---

## Phase 5: User Story 3 - LLM 与可观测迁移（Priority: P2）

**Goal**: ChatOpenAI 替换 LiteLLMProvider；审计/事件流适配 BaseMessage；token/费用/链路可观测仍准确

**Independent Test**: ChatOpenAI 装配后跑含工具任务，token/费用在事件流正确、事件序列与迁移前一致（quickstart S4/S5/S7）

### Tests for User Story 3（Test-First）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T022 [P] [US3] 测试于 `tests/unit/providers/test_chat_model.py`：`create_chat_model` 装配（model/api_key/base_url/bind_tools）；FakeChatLLM 下 `extract_usage` token/费用进入审计与事件（SC-005）

### Implementation for User Story 3

- [X] T023 [US3] 实现 `GSagent/core/providers/factory.py` 的 `create_chat_model(config, tools=None)`：ChatOpenAI 装配 + `bind_tools`（R-02，contracts/llm.md §1）
- [X] T024 [US3] 适配 `GSagent/config.py`：`create_tool_calling_llm()` 装配 `create_chat_model(cfg, tools=registry.get_all_tools())` + `create_tools_registry()` 注册全部 @tool 工具集 + 注入编排（R-02，contracts/llm.md §3）
- [ ] T025 [US3] 适配 `GSagent/core/agents/audit_mixin.py` 的 `_audit_model_call`：token 从 `AIMessage.response_metadata["token_usage"]`（经 `extract_usage`）→ 既有审计 payload（R-05）
- [ ] T026 [US3] 适配事件流：`LLM_RESPONSE` 的 `tokens_in/out/cost_usd` 从 `extract_usage`；`StreamMessage.ANSWER_END` 的 `messages` 快照经 `messages_to_dict()`（R-05，contracts/messages.md §3）
- [ ] T027 [US3] 适配截断/压缩：`SessionCompactor`/`ContextWindowLimiter` 内部消息改 BaseMessage（或经 `messages_to_dict`）（R-05）

**Checkpoint**: 三用户故事独立可测——LLM/审计/事件全 BaseMessage 化，外部契约不变

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 回归门禁、死代码清理、文档、端到端验证

- [X] T028 既有 105 测试全适配（dict 消息断言 → BaseMessage 断言）+ 全绿（SC-001/006，R-07 回归门禁）
- [ ] T029 移除死代码：`GSagent/core/providers/litellm_provider.py`（LiteLLMProvider）、`GSagent/core/tools/executor.py`（ToolExecutor）、`base.py`/`toolset.py`（Tool/Toolset）、`tests/helpers.py` 的 `ScriptedLLM`；检查引用后清理
- [ ] T030 更新文档：`CLAUDE.md`（架构表工具/LLM 层 langchain 化）、`README.md`（功能表）、新增 `docs/langchain-migration.md`
- [ ] T031 运行 `specs/002-langchain-ecosystem/quickstart.md` S1~S7 全部场景验证并修复（含 `agent run` ChatOpenAI 实测 V-01）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup（Phase 1）**: 无依赖，可立即开始
- **Foundational（Phase 2）**: 依赖 Setup —— **阻塞全部用户故事**（llm_adapter 是三层共同基础）
- **User Stories（Phase 3+）**: 全部依赖 Foundational
  - US1（消息/编排）与 US2（工具）存在文件交互（nodes.py 同时被两者改）——建议 US1 先行（MVP），US2 紧随
  - US3（LLM/可观测）依赖 US1 的节点适配（agent 用 ChatOpenAI）——US1 完成后可并行
- **Polish（Final Phase）**: 依赖全部 US 完成

### User Story Dependencies

- **User Story 1（P1）**: Foundational 后即可开始——**MVP 交付**（消息流 BaseMessage 化）
- **User Story 2（P1）**: Foundational 后即可开始——工具 @tool 化（registry 独立文件），与 US1 的 nodes.py 适配按 US1→US2 顺序
- **User Story 3（P2）**: 依赖 US1 的节点用 ChatOpenAI——US1 完成后可并行

### Within Each User Story

- 测试先写并 FAIL → 再实现
- 转换层/工厂 → 状态/节点 → 接入 → 集成验证
- 核心实现先于集成

### Parallel Opportunities

- Setup：T002 可并行（FakeChatLLM 独立文件）
- US1：T005（测试先行）与 T006 可并行
- US2：T014/T015（两个包装层独立文件）并行；T016~T019（四个工具集独立目录）并行
- US3：T022（测试先行）与 T023 可并行
- 各 US 内测试标记 [P] 可并行

---

## Parallel Example: User Story 2

```bash
# 守卫/审批包装层并行：
Task: "wrap_with_guards 在 GSagent/core/tools/registry.py"
Task: "wrap_with_approval 在 GSagent/core/tools/registry.py"
# 工具集迁移并行（独立目录）：
Task: "bash 工具集 @tool 化在 GSagent/plugins/toolsets/bash/"
Task: "filesystem 工具集 @tool 化在 GSagent/plugins/toolsets/filesystem/"
Task: "memory 工具集 @tool 化在 GSagent/plugins/toolsets/memory/"
Task: "sandbox 工具集 @tool 化在 GSagent/plugins/toolsets/sandbox/"
```

---

## Implementation Strategy

### MVP First（仅 User Story 1）

1. 完成 Phase 1: Setup（依赖 + FakeChatLLM）
2. 完成 Phase 2: Foundational（llm_adapter 转换层 + V-01/V-02）
3. 完成 Phase 3: User Story 1（消息流 BaseMessage 化）
4. **STOP and VALIDATE**: quickstart S1（回归）+ S2（消息转换）+ S5（事件流不变）
5. 交付 MVP：编排内部 BaseMessage 化，外部契约零破坏

### Incremental Delivery

1. Setup + Foundational → 转换层就位
2. US1 消息/编排 → 独立验证（MVP）
3. US2 工具 @tool → 独立验证（守卫/审批保持）
4. US3 LLM/可观测 → 独立验证
5. Polish 回归门禁/清理/文档

### 关键顺序约束（同文件避免冲突）

- `nodes.py` 被 US1（T008~T010）与 US2（T021 build_tool_node）修改 → US1 完成后 US2 再改
- `tool_calling_llm.py` 外壳不动（外部契约）；仅内部节点引用变化
- 工具迁移 T016~T019 各自独立目录，可并行；与编排接入 T021 分离

---

## Notes

- [P] 任务 = 不同文件、无依赖
- [Story] 标签映射到 spec.md 用户故事，可追溯
- **首个实现步骤**：T004 验证 V-01/V-02（deepseek tool calling 兼容 + ToolNode 审批语义），结论影响 T009/T021 具体实现
- 验证项/回退见 `research.md`（V-01/V-02）
- 测试遵守 CLAUDE.md：离线用 FakeChatLLM 打桩（R-06），LLM 相关打 `llm` marker
- 提交遵循 CLAUDE.md Git 工作流：`git commit -s --no-verify`
- 每个 checkpoint 停止并独立验证对应用户故事；105 既有测试全适配是最终回归门禁（T028）
