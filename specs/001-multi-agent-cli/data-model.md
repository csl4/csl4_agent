# Data Model: 多Agent CLI 框架

**Date**: 2026-09-02 | **Phase**: 1（/speckit-plan 输出）

> 实体与字段来源于 spec.md 的 Key Entities、需求 FR-001~FR-010 与 plan.md 的 Technical Context。
> 字段为逻辑描述，不含实现类型；验证规则标注对应需求/成功标准。

## 实体总览

```text
Session 1 ──< 1..* Agent (角色实例)
Agent 1 ──< 0..* Task (A2A 任务/子任务)
Task 1 ──< 0..* Message (Part)
Agent 1 ──< 0..* CommandExecution
Agent 1 ──< 0..* LogEntry / HistoryRecord
Skill 0..* ──> Agent (注入)
EnvironmentInfo 1 ──> Agent (上下文)
```

## 实体定义

### Session（会话）

用户与系统的多轮对话上下文，承载输入/输出接口之间的状态（FR-009）。

| 字段 | 说明 | 验证 |
|------|------|------|
| id | 会话唯一标识 | 唯一性约束 |
| messages | 对话消息序列（含压缩标记） | FR-009：超限压缩而非丢弃 |
| agents | 本次会话参与的 Agent 角色实例 | FR-001 |
| created_at / updated_at | 时间戳 | 只读 |

**状态机**: active → compacted（压缩后仍 active）→ closed / aborted（中断清理，Edge Case）。

### Agent（Agent 角色）

主 / 业务 / 编排 / SubAgent 的统一抽象（FR-001，宪法 V 最通用层级）。

| 字段 | 说明 | 验证 |
|------|------|------|
| role | 角色类型：main / orchestrator / business / subagent | 枚举；SubAgent 动态创建 |
| id / name | 实例标识与可读名 | 会话内唯一 |
| context | 该角色的推理与执行上下文（消息窗口） | FR-009 |
| parent_agent | 创建者（SubAgent 必填，指向编排或主 Agent） | FR-001 动态创建语义 |
| capabilities | 可调用工具/技能集合 | 宪法 I 插件接入 |

### A2A Task（任务 / 子任务）

Agent 间通过 A2A 协议交换的工作单元（plan.md 决策 1/2）。

| 字段 | 说明 | 验证 |
|------|------|------|
| id | A2A 任务唯一标识 | 唯一性 |
| state | submitted / working / input-required / completed / failed / canceled | A2A 状态机 |
| parts | 输入/输出内容（text/file/data） | 消息 Part 类型 |
| created_by / assigned_to | 发起与承接 Agent | FR-001 |
| created_at / updated_at | 时间戳 | 只读 |

**状态机（A2A 标准）**:
`submitted → working ⇄ input-required → completed | failed | canceled`
（Task 生命周期，见 [contracts/a2a.md](./contracts/a2a.md)）

### CommandExecution（命令执行记录）

一条命令及其输出、状态、耗时、执行环境（FR-005/FR-008，spec.md Key Entities）。

| 字段 | 说明 | 验证 |
|------|------|------|
| id | 执行记录唯一标识 | 唯一性 |
| command | 实际执行的命令（按目标终端改写后） | FR-002 终端适配 |
| shell | 执行终端类型（powershell/bash/zsh） | FR-002 |
| output / exit_code | 输出与退出状态 | FR-005 结果整理 |
| duration_ms | 耗时 | 性能观测 |
| sandbox_used | 是否沙箱执行 | FR-004 |
| approved | 是否经用户确认 | FR-003：高风险 100% 确认 |

### Skill（技能）

从本地技能库注入的可复用能力定义（FR-006）。

| 字段 | 说明 | 验证 |
|------|------|------|
| name | 技能唯一名 | 唯一性；技能库匹配键 |
| description | 适用场景描述 | 匹配依据 |
| instructions | 注入 Agent 上下文的指令 | FR-006 |
| tool_bindings | 绑定的工具/工具集 | 宪法 I 插件接入 |

### EnvironmentInfo（环境信息）

启动时采集的环境快照，纳入任务上下文（FR-007）。

| 字段 | 说明 | 验证 |
|------|------|------|
| shell | 终端类型（探测结果） | FR-002 |
| cwd | 当前工作目录 | FR-007 与真实环境一致 |
| available_tools | 可用工具清单 | FR-007 |
| platform | 操作系统 | 环境适配 |

### LogEntry / HistoryRecord（日志 / 历史）

持久化的会话与执行记录，支持查询与审计（FR-008，SC-005）。

| 字段 | 说明 | 验证 |
|------|------|------|
| id | 记录唯一标识 | 唯一性 |
| session_id / agent_id | 归属会话与 Agent | 关联 |
| type | session / command / event | 分类 |
| payload | 记录内容（JSON） | — |
| created_at | 时间戳 | SC-005：90% 查询 ≤2 秒 |

## 验证规则汇总（映射到需求）

- FR-001: Session↔Agent 一对多、SubAgent 必须有 parent_agent。
- FR-003: CommandExecution.approved 对高风险命令 MUST 为 true 才执行（SC-003 0 例未授权）。
- FR-008: 每次 CommandExecution 与 Session 关闭 MUST 落 LogEntry/HistoryRecord。
- FR-009: Session 上下文超限时 MUST 压缩而非清空（compaction 语义）。

## 未决项（实现阶段核实）

- ~~TODO(A2A_SCHEMA)~~ **已对齐（T037）**: a2a-sdk 的 Task/Message/Part 具体 schema
  字段名以实际安装的 `a2a-sdk==1.0.0a2` protobuf 类型为准，全量集成测试验证通过
  （见 contracts/a2a.md 验证节与 research.md §Follow-up）。
