# Research: 多Agent CLI 框架

**Date**: 2026-09-02 | **Phase**: 0（/speckit-plan 输出）

> ⚠️ **网络限制说明**: 本环境 WebSearch/WebFetch 被禁用（403/域名校验失败）。
> 以下 A2A 相关信息基于既有知识整理；版本号与 API 细节需在实现阶段联网核实
> （follow-up 见文末）。

## 1. A2A 开源框架选型

- **Decision**: 采用 **a2a-sdk**（A2A / Agent2Agent 官方开源 Python SDK，github.com/a2aproject）
- **Rationale**:
  - A2A 是 Google 主导、已捐赠 Linux Foundation 的开放 Agent 互操作协议（2025 发布），
    与本需求"多agent协议可以使用a2a 架构 使用开源的框架"字面一致。
  - 官方 SDK 提供协议类型（AgentCard / Task / Message / Part）与
    client/server 实现，跨 Python/Java/TypeScript，生态完整、维护活跃。
  - 支持"主 Agent 编排工作 Agent"的 supervisor 模式，与"主→编排→业务→SubAgent"拓扑天然契合。
- **Alternatives considered**:
  - CrewAI / AutoGen(AG2) / LangGraph: 通用多Agent 框架，但非 A2A 协议，绑定各自编排模型，
    违背"使用 a2a 架构"的明确要求。
  - 自实现最小 A2A 协议层: 可控但重复造轮子，违背宪法 V"复用、不手写"精神。
  - MCP: 是"工具协议"而非"Agent 互操作协议"，不解决 Agent 间协作。

### A2A 核心模型（本特性采用的协议事实）

- **AgentCard**: Agent 发现/自描述元数据（能力、URL、认证）；主 Agent 据此发现工作 Agent。
- **Task 生命周期**: submitted → working → input-required → completed / failed / canceled。
- **Message / Part**: Agent 间交换内容（text / file / data 三种 Part）。
- **传输**: JSON-RPC 2.0 over HTTP + SSE 流式；本特性 v1 以**进程内**方式调用协议模型，
  保留 HTTP 端点扩展点（`a2a/server.py` 为可选，不阻塞 v1 交付）。

## 2. 业务 Agent 与 编排 Agent 职责划分（NEEDS CLARIFICATION 的默认决策）

- **Decision**: 编排 Agent 负责**任务拆解 + 并行调度 + SubAgent 生命周期**；业务 Agent 负责
  **业务/领域任务处理**（纯执行方）。主 Agent（现有 agent）负责用户交互与整体触发。
- **Rationale**: 符合 orchestrator/worker（监督者-工人）模式：调度与领域逻辑分离，单一职责，
  与 A2A supervisor 模型一致；主 Agent 保持薄、只做交互与终局决策，与宪法 II（CLI 解耦）一致。
- **Alternatives considered**:
  - 编排 Agent 兼任业务逻辑（Option B）: 职责过重，违反单一职责，不利于并行演进。
  - 业务 Agent 自己拆解（Option C）: 业务 Agent 需感知调度细节，与 A2A 编排模型冲突。
- **返工风险提示**: 用户在 `/speckit-clarify` 中未回答此问题即进入计划。若后续明确为其他划分，
  影响集中在 orchestrator/business_agent 的职责归属与 FR-001/FR-010 措辞，不影响 A2A 协议层。

## 3. 现有单 Agent 如何升级为主 Agent

- **Decision**: 现有 `ToolCallingLLM`（`agent/core/tool_calling_llm.py` 主循环）保留为底层
  执行引擎；新增 `BaseAgent`（`core/agents/base_agent.py`）抽象角色，`MainAgent` 包装
  `ToolCallingLLM` 作为主 Agent 运行骨架。
- **Rationale**:
  - `ToolCallingLLM` 已实现 审批→LLM→并行执行→压缩→事件流 的完整循环，直接复用。
  - 其生成器事件流（`StreamMessage`，含 APPROVAL_REQUIRED / FRONTEND_PAUSE 中断态）
    是 CLI 契约（宪法 II）——主 Agent 包装它意味着 CLI 事件协议零改动。
  - 复用 `Config` 装配根（`create_tool_calling_llm` 等）注入依赖，符合现有"组合根"模式。
- **Alternatives considered**: 重写新主循环 — 破坏现有事件契约，返工风险高，否决。

## 4. 轻量沙箱方案

- **Decision**: v1 采用**轻量沙箱**：在现有 bash 工具集校验层之上，用子进程隔离
  （`subprocess` + 受控工作目录 + 超时/资源上限）作为第一道隔离；容器化沙箱（Docker）
  作为后续增强，不改 v1 契约。
- **Rationale**: 宪法"v1 默认轻量沙箱"假设；现有 bash 工具集已有命令校验/白名单/审批
  （`validation.py`、`command_arg_rules.py`、`common/default_lists.py`），安全基线已存在，
  轻量沙箱增量最小、跨平台（Windows/Linux/macOS）一致。
- **Alternatives considered**: Docker 容器化 — Windows/macOS 需 Docker Desktop，破坏开箱即用
  （SC-004），延迟到 v2。

## 5. 环境适配（PowerShell / bash / zsh）

- **Decision**: 保留现有 bash 适配为基座，新增终端类型探测（`shell` 检测）与命令写法映射表；
  同义命令按目标终端改写（如 `ls` ↔ `Get-ChildItem`），失败时以目标终端正确写法重试。
- **Rationale**: SC-004 要求三终端开箱即用；命令差异集中（路径分隔、别名、管道语法），
  用映射表 + 重试（tenacity）成本最低。
- **Alternatives considered**: 为每终端写独立执行器 — 重复度高，否决。

## 6. 技能注入与存储

- **Decision**: 技能库 = 本地 YAML/JSON 目录，技能定义含 name/description/instructions/工具绑定；
  环境信息库 = 启动时采集的环境快照（cwd、shell、可用工具）；日志/历史库 = 追加式 JSONL 文件。
- **Rationale**: 宪法假设"本地持久化、无外部服务"；YAML 与现有 `yaml_loader.py`/config 模式一致；
  JSONL 便于追加与检索，满足 SC-005（2 秒内查询）。
- **Alternatives considered**: SQLite — 查询更强但引入新依赖，v1 文件足够，v2 可迁。

## Follow-up TODOs

- TODO(A2A_VERIFY): 联网核实 a2a-sdk 当前版本、包名（`a2a-sdk` / `a2a`）、Python 3.10 兼容性
  与 client/server API 签名后锁定依赖版本。
- TODO(TERMINAL_MAP): 实现阶段补充三终端命令差异映射表的完整用例（bash/zsh 差异较小，
  PowerShell 差异较大）。

## 决策汇总

| # | 未知项 | 决策 |
|---|--------|------|
| 1 | A2A 框架 | a2a-sdk（官方开源 SDK） |
| 2 | 业务/编排分工 | 编排=拆解/调度；业务=领域处理（orchestrator/worker） |
| 3 | 主 Agent | 包装现有 ToolCallingLLM，事件契约不变 |
| 4 | 沙箱 | v1 轻量沙箱（子进程隔离），容器化 v2 |
| 5 | 环境适配 | 终端探测 + 命令映射 + tenacity 重试 |
| 6 | 存储 | 本地 YAML/JSON + JSONL，无外部服务 |
