---

description: "Task list for 多Agent CLI 框架 (multi-agent-cli)"
---

# Tasks: 多Agent CLI 框架

**Input**: Design documents from `/specs/001-multi-agent-cli/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: 测试任务已包含——依据宪法 IV「Test-First (NON-NEGOTIABLE)」：新功能需单测，新插件/toolset 需集成测试；HTTP mock 用 `responses`。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- 单项目结构：`agent/`（源码）、`tests/`（测试）位于仓库根（见 plan.md「Project Structure」）
- 契约文件：`specs/001-multi-agent-cli/contracts/`（cli.md / a2a.md / config.md）

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与基础结构

- [X] T001 按 plan.md 结构创建包目录及 `__init__.py`：`agent/core/agents/`、`agent/core/a2a/`、`agent/core/skills/`、`agent/core/history/`、`agent/core/env/`、`agent/plugins/toolsets/sandbox/`、`tests/unit/`、`tests/integration/`
- [X] T002 在 pyproject.toml 增加 `a2a-sdk` 依赖（poetry add a2a-sdk；版本按 research.md TODO(A2A_VERIFY) 联网核实后锁定）
- [X] T003 [P] 在 pyproject.toml `[tool.pytest.ini_options]` 将 `tests/unit`、`tests/integration` 加入 testpaths，确认 `llm` marker 保留
- [X] T004 [P] 在 `agent/core/env/__init__.py` 预声明终端适配模块导出占位（配合 US2 T019）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事的前置核心——A2A 协议基类、Agent 抽象、配置向后兼容

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 实现 A2A 协议模型 `agent/core/a2a/protocol.py`：AgentCard / Task（含 submitted→working⇄input-required→completed|failed|canceled 状态机）/ Message / Part(text|file|data)，字段与 data-model.md「A2A Task」及 contracts/a2a.md 对齐
- [X] T006 实现 `agent/core/agents/base_agent.py` 的 BaseAgent 抽象：role 枚举（main/orchestrator/business/subagent）、上下文窗口、send/receive 消息、tenacity 重试封装（宪法 V 最通用层级）
- [X] T007 在 `agent/config.py` 增加 `multi_agent` 配置段：DEFAULT_CONFIG 默认值 + 解析（Pydantic `extra="allow"` + `model_validator` 映射旧名，宪法 III）+ 环境变量 `AGENT_MULTI_AGENT`/`AGENT_MAX_SUBAGENTS` 覆盖，字段见 contracts/config.md
- [X] T008 [P] 单测 A2A 协议模型（序列化往返、状态机合法性）在 `tests/unit/test_a2a_protocol.py`
- [X] T009 [P] 单测配置向后兼容（旧 config.yaml 无 multi_agent 段 → 零告警加载、行为不变）在 `tests/unit/test_config_compat.py`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - 多Agent协作执行混合任务 (Priority: P1) 🎯 MVP

**Goal**: 主 Agent（现有 agent）→ 编排 Agent（拆解/调度）→ 业务 Agent（领域处理）→ 动态 SubAgent（执行）的端到端多Agent 链路；复用现有 ToolCallingLLM 与 Tool/Toolset 执行层。

**Independent Test**: 输入一个含 ≥2 条命令的混合任务（如磁盘占用分析+清理），系统自动协调四角色完成并以结构化自然语言返回；全程无需手动拆解命令。

### Tests for User Story 1（宪法 IV 强制）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T010 [P] [US1] A2A 通信单测（进程内 send/stream 任务往返）在 `tests/unit/test_a2a_client.py`
- [X] T011 [P] [US1] 端到端混合任务集成测试（LLM 用 `responses` mock、走真实 bash toolset）在 `tests/integration/test_multi_agent_flow.py`

### Implementation for User Story 1

- [X] T012 [P] [US1] 实现进程内 A2A client `agent/core/a2a/client.py`：向工作 Agent 发送/流式 Task（对齐 contracts/a2a.md）
- [X] T013 [P] [US1] 实现 MainAgent `agent/core/agents/main_agent.py`：包装 ToolCallingLLM、用户交互、触发编排、终局决策（research.md §3）
- [X] T014 [P] [US1] 实现 Orchestrator `agent/core/agents/orchestrator.py`：任务拆解、并行调度、SubAgent 生命周期（research.md §2）
- [X] T015 [P] [US1] 实现 BusinessAgent `agent/core/agents/business_agent.py`：业务/领域任务处理（research.md §2）
- [X] T016 [P] [US1] 实现 SubAgent `agent/core/agents/subagent.py`：动态创建、经现有 ToolExecutor 执行子任务/命令（FR-001）
- [X] T017 [US1] 在 `agent/utils/stream.py` 增加多Agent 事件类型（任务拆解、SubAgent 启停、任务完成），向后兼容既有 StreamMessage 事件（宪法 II，contracts/cli.md）
- [X] T018 [US1] 接线 CLI `agent/main.py`：`agent chat --multi-agent` 标志 + `agent agents list` 子命令（contracts/cli.md），Config 装配经 `create_tool_calling_llm` 注入（复用现有组合根）
- [X] T019 [US1] 结果归并：SubAgent/命令输出整理为结构化自然语言回复（FR-005, SC-006）

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - 安全执行与环境适配 (Priority: P2)

**Goal**: 终端自动探测（PowerShell/bash/zsh）与命令映射；轻量沙箱执行；高风险命令 100% 确认（FR-002/003/004, SC-003/004）。

**Independent Test**: 任一支持终端下提交高风险命令触发确认/拦截；同义命令在 PowerShell/bash/zsh 下自动用对应写法执行；沙箱不可用时有明确错误提示。

### Tests for User Story 2（宪法 IV 强制）⚠️

- [X] T020 [P] [US2] 单测终端探测与命令映射（三终端同义命令）在 `tests/unit/test_terminal.py`
- [X] T021 [P] [US2] 集成测试沙箱执行 + 高风险命令确认/拦截在 `tests/integration/test_safety_sandbox.py`

### Implementation for User Story 2

- [X] T022 [P] [US2] 实现终端探测与命令映射 `agent/core/env/terminal.py`：检测 shell 类型、同义命令改写表（如 `ls`↔`Get-ChildItem`）、失败时以目标终端写法重试（tenacity，research.md §5）
- [X] T023 [P] [US2] 实现轻量沙箱 toolset `agent/plugins/toolsets/sandbox/sandbox_toolset.py`：子进程隔离 + 超时 + 受控工作目录 + 资源上限；在 `agent/plugins/toolsets/__init__.py` 的 BUILTIN_PYTHON_TOOLSETS 注册（宪法 I 插件优先，research.md §4）
- [X] T024 [US2] 接线安全防护：高风险命令经现有 bash toolset 审批层 + 沙箱执行（FR-003）；沙箱不可用 → 明确错误提示而非静默失败（FR-004）
- [X] T025 [US2] 暴露沙箱/超时配置 `agent/config.py`（`multi_agent.sandbox`，见 contracts/config.md）
- [X] T026 [US2] 终端适配接入命令执行路径：SubAgent/命令执行前探测 shell 并按映射改写（FR-002）

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - 技能注入与知识复用 (Priority: P3)

**Goal**: 本地技能库/环境信息注入 Agent 上下文；执行日志与会话历史持久化与查询（FR-006/007/008, SC-005）。

**Independent Test**: 添加技能后下达匹配任务 → 自动应用并说明；询问环境信息 → 与真实环境一致；`agent history` 查询 ≤2 秒返回。

### Tests for User Story 3（宪法 IV 强制）⚠️

- [X] T027 [P] [US3] 单测技能库匹配、环境信息采集、历史存储/查询在 `tests/unit/test_skills_history.py`
- [X] T028 [P] [US3] 集成测试技能触发任务 + 历史查询流程在 `tests/integration/test_skills_history_flow.py`

### Implementation for User Story 3

- [X] T029 [P] [US3] 实现技能库 `agent/core/skills/library.py`：加载 YAML 技能（name/description/instructions/tool_bindings，data-model.md Skill）、按任务匹配、注入上下文（FR-006）
- [X] T030 [P] [US3] 实现环境信息快照 `agent/core/skills/env_info.py`：cwd、shell、可用工具、平台（FR-007，data-model.md EnvironmentInfo）
- [X] T031 [P] [US3] 实现历史存储 `agent/core/history/store.py`：JSONL 追加式持久化会话/命令执行记录 + 查询接口（FR-008，SC-005）
- [X] T032 [US3] 接线技能注入与环境信息到 Agent 上下文（MainAgent/Orchestrator 组装时注入，FR-006/007）
- [X] T033 [US3] CLI 子命令 `agent skills list|add|rm`、`agent history session|command` 在 `agent/main.py`（contracts/cli.md）
- [X] T034 [US3] 执行记录落库：每次 CommandExecution 与 Session 关闭 MUST 写入历史（data-model.md 验证规则，FR-008）

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事收尾

- [X] T035 [P] 文档更新：README 功能表 + `docs/` 特性页 + mkdocs 导航注册（CLAUDE.md「Adding a New Integration」清单）
- [X] T036 [P] 运行 `specs/001-multi-agent-cli/quickstart.md` 全部验证场景并记录结果（手动端到端 + 自动化测试）
- [X] T037 清理与对齐：消除实现阶段遗留 TODO（如 research.md TODO(A2A_VERIFY) 联网核实后锁定依赖）、确认无遗留占位符、契约与实现一致

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - US1（P1）先行，US2/US3 可在其基础上并行
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: 依赖 Foundational（A2A 协议 + BaseAgent + 配置）；无其他故事依赖 —— MVP
- **User Story 2 (P2)**: 依赖 Foundational + US1（接入 SubAgent 执行路径 T026）；可独立验证安全与终端行为
- **User Story 3 (P3)**: 依赖 Foundational + US1（注入 MainAgent/Orchestrator 上下文 T032）；技能/历史可独立验证

### Within Each User Story

- Tests MUST be written and FAIL before implementation（宪法 IV）
- 协议/模型 → 服务/编排 → 执行接线 → CLI 集成
- Story complete before moving to next priority

### Parallel Opportunities

- Setup 中 T003/T004 标记 [P] 可并行
- Foundational 中 T008/T009 标记 [P] 可并行
- US1 中 T012~T016（各 Agent 文件独立）可并行；T010/T011 测试先行
- US2 中 T022/T023（终端 vs 沙箱独立文件）可并行
- US3 中 T029/T030/T031（技能/环境/历史独立）可并行
- 不同用户故事可交由不同实现者并行推进（共享执行路径处需协调）

---

## Parallel Example: User Story 1

```bash
# Launch all agents' implementation together (different files, no deps):
Task: "Implement MainAgent in agent/core/agents/main_agent.py"        # T013
Task: "Implement Orchestrator in agent/core/agents/orchestrator.py"   # T014
Task: "Implement BusinessAgent in agent/core/agents/business_agent.py" # T015
Task: "Implement SubAgent in agent/core/agents/subagent.py"            # T016

# Launch tests first (TDD per Constitution IV):
Task: "A2A client unit test in tests/unit/test_a2a_client.py"          # T010
Task: "End-to-end flow integration test in tests/integration/test_multi_agent_flow.py" # T011
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational（CRITICAL - blocks all stories）
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: 运行 quickstart.md 场景 1，独立验证 US1
5. Demo if ready —— 多Agent 编排链路（含命令执行）即可演示

### Incremental Delivery

1. Setup + Foundational → Foundation ready（A2A 协议 + Agent 抽象 + 配置）
2. User Story 1 → 多Agent 协作执行（MVP）
3. User Story 2 → 安全执行与环境适配
4. User Story 3 → 技能注入与知识复用
5. Each story adds value without breaking previous stories

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1（主链）
   - Developer B: User Story 2 的前置（终端/沙箱模块，T022/T023）
   - Developer C: User Story 3 的前置（技能/环境/历史模块，T029/T030/T031）
3. 共享执行路径（T024/T026/T032）在 US1 完成后汇合

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- 测试先行（宪法 IV）：实现前测试须 FAIL
- Commit after each task or logical group（宪法 Git 工作流：`git commit -s --no-verify`）
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- 实现前联网核实 a2a-sdk 版本与 API 签名（research.md TODO(A2A_VERIFY)）
