# Implementation Plan: 多Agent CLI 框架

**Branch**: `001-multi-agent-cli` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-multi-agent-cli/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

将当前的"工具执行 + 单Agent"CLI 框架（`ToolCallingLLM` 主循环 + `Tool`/`Toolset` 插件体系）
升级为 A2A 架构的四角色多Agent 系统：**主 Agent（现有 agent 兼任，负责用户交互）→ 编排 Agent（任务拆解/并行调度）→ 业务 Agent（领域处理）→ 动态 SubAgent（子任务与命令执行）**。
Agent 间通信遵循开源 A2A（Agent2Agent）协议模型；命令执行复用现有 `Tool`/`Toolset`（bash/filesystem）与安全审批层（bash 工具集的校验/白名单），沙箱 v1 采用轻量沙箱。

## Technical Context

**Language/Version**: Python ^3.10（pyproject.toml 已锁定）

**Primary Dependencies**: 现有（typer、pydantic v2、litellm、tenacity、pyyaml、httpx、jinja2、rich、bashlex）
+ 新增 **a2a-sdk**（开源 A2A 协议 SDK，见 research.md）

**Storage**: 本地文件（YAML/JSON）持久化——技能库、环境信息库、执行日志/历史库；无外部服务依赖

**Testing**: pytest + pytest-asyncio + responses（HTTP mock 强制 `responses`）；新功能单测、新插件/toolset 集成测试；LLM 相关打 `llm` 标记单独跑

**Target Platform**: Windows（当前开发）/ macOS / Linux；终端 PowerShell / bash / zsh

**Project Type**: CLI 应用 + 库（agent 框架）

**Performance Goals**: 混合任务端到端 ≤ 5 分钟（SC-001）；一次回复内给出结构化结果（SC-006）；历史查询 90% 在 2 秒内（SC-005）

**Constraints**: 跨终端自动适配；v1 轻量沙箱；本地优先离线可用；配置向后兼容（宪法 III）；CLI 只消费 `StreamMessage` 事件流（宪法 II）

**Scale/Scope**: 4 个固定 Agent 角色 + 动态 SubAgent；单 CLI 会话；混合任务一次性成功率 ≥ 80%（SC-002）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | 来源 | 状态 |
|------|------|------|
| 每个能力以插件/toolset 形式提供，核心引擎不依赖具体工具细节 | 宪法 I 插件优先 | 通过：新 Agent 角色为 core 层编排概念，命令执行仍走现有 Tool/Toolset |
| CLI 只消费 `StreamMessage` 事件流，不接触底层 Tool/Toolset | 宪法 II CLI 接口 | 通过：多Agent 层在 core 之下，CLI 契约不变 |
| 配置重命名用 Pydantic `extra="allow"` + `model_validator`，不留废弃字段 | 宪法 III 向后兼容 | 通过：新增 agent/multi_agent 配置段，不改动现有字段名 |
| 新功能需单测；新插件需集成测试；HTTP mock 用 `responses` | 宪法 IV 测试优先 | 通过：orchestrator/agents 单测 + A2A 通信集成测试 |
| 重试用 `tenacity`，不手写循环 | 宪法 V 通用化 | 通过：Agent 间调用与 A2A 重试统一 tenacity |
| 新字段/方法放在类层次最通用层级 | 宪法 V 通用化 | 通过：`BaseAgent` 抽象承载公共编排行为 |
| 不主动运行 pre-commit/ruff/mypy 除非用户要求 | 工程标准 | 通过（开发约束） |

## Project Structure

### Documentation (this feature)

```text
specs/001-multi-agent-cli/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
agent/
├── core/
│   ├── agents/                    # NEW: 多Agent 编排层
│   │   ├── __init__.py
│   │   ├── base_agent.py          # BaseAgent: 角色/上下文/回话抽象（最通用层级）
│   │   ├── main_agent.py          # 主 Agent：包装 ToolCallingLLM，用户交互 + 触发编排
│   │   ├── orchestrator.py        # 编排 Agent：任务拆解、并行调度、SubAgent 生命周期
│   │   ├── business_agent.py      # 业务 Agent：业务/领域任务处理
│   │   └── subagent.py            # SubAgent：动态创建、命令/子任务执行
│   ├── a2a/                       # NEW: A2A 协议层
│   │   ├── __init__.py
│   │   ├── protocol.py            # A2A Message/Task/AgentCard 模型（对接 a2a-sdk）
│   │   ├── client.py              # 向工作 Agent 发送/流式任务
│   │   └── server.py              # 可选：HTTP Agent 端点（AgentCard 暴露）
│   ├── skills/                    # NEW: 技能注入
│   │   ├── __init__.py
│   │   ├── library.py             # 本地技能库加载/匹配
│   │   └── env_info.py            # 环境信息快照采集
│   ├── history/                   # NEW: 日志/历史存储
│   │   ├── __init__.py
│   │   └── store.py               # 会话/执行记录持久化与查询
│   ├── (existing) llm.py / models.py / tool_calling_llm.py / tool_executor.py / tools.py
│   └── (existing) conversations.py / prompt_components.py / truncation/
├── plugins/
│   ├── toolsets/
│   │   ├── bash/                  # existing：命令执行 + 安全审批/校验层
│   │   ├── filesystem/            # existing
│   │   └── sandbox/               # NEW: 轻量沙箱 toolset（安全防护）
│   └── (existing) interfaces.py / yaml_loader.py
├── config.py                      # +multi_agent 配置段（向后兼容）
└── main.py                        # +多Agent 子命令（CLI 契约不变）

tests/
├── unit/                          # NEW: agents / a2a / skills / history 单测
├── integration/                   # NEW: A2A 通信、端到端混合任务
└── llm/                           # (optional) llm 标记测试
```

**Structure Decision**: 单一项目（Option 1，默认）。多Agent 编排属 core 引擎层（`agent/core/agents/`），
A2A 协议独立成 `agent/core/a2a/` 便于后续抽离为共享库；技能/历史为支撑模块；轻量沙箱按插件优先原则
做成 `Toolset`（`agent/plugins/toolsets/sandbox/`），复用现有 `Tool`/`Toolset` 审批与加载机制。

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

无违例，本表留空。
