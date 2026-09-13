# 项目需求文档（Requirements）

> **项目**：通用 LLM Agent 框架（`GSagent`）
> **文档版本**：v1.0 ｜ **日期**：2026-09-13 ｜ **状态**：草稿（Draft）
> **关联契约**：`specs/001-multi-agent-cli`、`specs/002-enterprise-cli-upgrade`、`specs/003-optimize-model-hierarchy`、`specs/004-memory-isolation`
> **关联文档**：`CLAUDE.md`（项目说明）、`GSDOC/agent1.md`（对象模型草案）

---

## 目录

1. [项目概述](#1-项目概述)
2. [目标用户与使用场景](#2-目标用户与使用场景)
3. [功能需求](#3-功能需求)
4. [非功能需求](#4-非功能需求)
5. [配置需求](#5-配置需求)
6. [数据需求](#6-数据需求)
7. [本次会话确认的增量需求](#7-本次会话确认的增量需求)
8. [未完成项与待办](#8-未完成项与待办)
9. [验收标准与成功指标](#9-验收标准与成功指标)

---

## 1. 项目概述

### 1.1 定位

**通用 LLM Agent 框架**（Python 3.10+），面向**本地优先、离线可测、跨终端自动适配**的智能体应用：

- **命令行交互**：Typer 驱动的 `chat`（交互）/ `run`（单次）/ `serve`（服务）入口
- **工具执行**：插件化 `Toolset` / `Tool` 架构，内置 bash / filesystem / sandbox / memory 工具集
- **多 Agent 编排**：A2A 协议（主 / 编排 / 业务 / 动态 SubAgent 四角色）协作拆解并行任务
- **企业级安全策略层**：HITL 审批三态 / 路径与命令守卫 / 全量审计（脱敏）
- **本地数据化**：配置、日志、审计、记忆、快照等全部落在**项目内** `./.GSagent/`，不写用户主目录

### 1.2 目标

1. 让开发者/业务员用自然语言描述任务，系统自动完成"推理 + 工具执行 + 多 Agent 协作"，无需手动拆解命令
2. 提供企业级安全基线：危险操作有守卫拦截、有审批确认、有全量审计留痕
3. 支持任务可拆解并行执行（多 Agent 编排 / Plan-and-Execute）、可追溯（审计/可观测/历史）、可恢复（快照/持久化任务队列）
4. 提供记忆（长期 + 会话）、技能库、评估体系，支撑持续迭代与知识复用

### 1.3 业务场景

以**关务 / 业务单据**类业务为参考场景（`specs/002`）：读取申报数据 → 校验必填列 → 生成草稿等含先后依赖的多步任务；审计与用量满足企业合规要求。

### 1.4 范围边界

- **本期不包含**：GUI、远程/云环境部署（K8s 后续）、团队协作、容器化沙箱（v2 增强）
- **单机优先**：分布式可观测为后续增量（契约先定义，本期仅落地单机）
- **跨终端**：PowerShell / bash / zsh 三种终端自动探测与命令改写

---

## 2. 目标用户与使用场景

### 2.1 目标用户

| 用户 | 关注点 |
|---|---|
| 业务/操作员 | 用自然语言完成任务，危险操作有确认，结果结构化 |
| 审批人 | 对高风险操作进行人工审批（HITL） |
| 运维/审计 | 全量审计留痕、用量与成本、异常告警、链路追溯 |
| 开发者 | 可扩展工具集/技能、离线可测、代码结构清晰、依赖可复现 |

### 2.2 典型使用场景

1. **混合任务协作**（P1）：输入"分析当前目录磁盘占用并清理超过 100MB 的临时文件"，系统自动协调主/业务/编排 Agent，动态创建 SubAgent 执行命令，结构化回复
2. **安全执行**（P2）：高风险命令执行前弹窗确认；跨终端自动改写命令
3. **技能复用**（P3）：本地技能库自动注入匹配技能；环境信息（目录/工具）进上下文
4. **企业审计**（P1）：任意任务可追溯模型 I/O、工具调用、审批决策，密钥零明文
5. **多模式执行**（P2）：`--plan` 模式先规划 DAG 再按依赖批次并行执行
6. **服务集成**（P3）：`serve` 提供线程/回合/SSE API + 持久化后台任务
7. **持续迭代**（P4）：真实业务样本评估集跑三指标，回归对比基线；快照保障试错可回退
8. **多用户隔离**（P1）：同一项目下不同用户记忆互不可见

---

## 3. 功能需求

> 需求编号按功能域组织；来源列标注出处 spec。优先级 P0=必须 / P1=高 / P2=中 / P3=低。

### 3.1 CLI 交互（`GSagent/main.py`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-CLI-01 | MUST 提供 `chat`（交互，默认命令），支持斜杠命令 `/exit` `/hitl <mode>` `/remember` `/memory` `/tool` `/skill` | P0 | 001 |
| FR-CLI-02 | MUST 提供 `run`（单次执行）：`--prompt-file` / `--file` / `--json-output-file` / `--snapshot` / `--hitl`，支持管道 stdin 输入 | P0 | 001 |
| FR-CLI-03 | MUST 提供 `serve`（FastAPI：线程/回合/SSE + 后台任务），`--host` / `--port` / `--hitl=never|auto` | P1 | 002 |
| FR-CLI-04 | MUST 提供管理子命令：`toolset`（列出工具集）、`agents list`（多 Agent 角色）、`skills list/add/rm`、`history session/command/usage`、`tasks list/cancel`、`snapshot list/restore`、`eval run/baseline/list`、`version` | P1 | 001/002 |
| FR-CLI-05 | MUST 保持命令行入口可用：`agent <command>`（console_scripts）；CLI 只消费 `StreamMessage` 事件流，不接触底层 Tool/Toolset 细节（宪法 II） | P0 | 本项目 |
| FR-CLI-06 | MUST 支持 `--multi-agent` / `--plan` / `--max-subagents` / `--hitl` 等模式开关 | P0 | 001/002 |

### 3.2 工具执行（`GSagent/core/tools/` + `plugins/toolsets/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-TOOL-01 | MUST 提供 `ToolExecutor`：工具注册索引、懒初始化、标签过滤（CLI=CORE+CLI，server=CORE+CLUSTER）、MCP 名字前缀去冲突 | P0 | 001 |
| FR-TOOL-02 | MUST 提供插件化工具集：内置 Python 工具集（bash / filesystem / sandbox / memory）注册于 `BUILTIN_PYTHON_TOOLSETS`，工厂 `(install_config) -> Toolset|None` | P0 | 001 |
| FR-TOOL-03 | MUST 支持 YAML 工具集加载（`plugins/toolsets/*.yaml`） | P2 | 001 |
| FR-TOOL-04 | MUST 提供工具基类模板方法：审批 → 校验 → 执行 → 结果变换（`Tool` + `Transformer`） | P0 | 001 |
| FR-TOOL-05 | MUST 支持工具结果变换器（`JsonTruncationTransformer` / `LineCountTransformer`）限流，防超大输出淹没上下文 | P2 | 001 |
| FR-TOOL-06 | MUST 工具执行挂企业级守卫/审批/审计注入点（见 3.4） | P0 | 002 |

### 3.3 多 Agent 编排（`GSagent/core/agents/` + `core/a2a/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-MA-01 | MUST 支持四角色协作：主 Agent（用户交互+终局归纳）/ 编排 Agent（拆解+并行调度）/ 业务 Agent（领域分析）/ 动态 SubAgent（命令子任务） | P0 | 001 |
| FR-MA-02 | MUST 由用户自然语言触发，系统自动拆解任务并动态创建 SubAgent，无需用户手动分派 | P0 | 001 |
| FR-MA-03 | MUST 命令子任务经 SubAgent 执行：结构化 JSON 描述符 / 命令前缀启发式 / sandbox 优先回退 bash | P0 | 001 |
| FR-MA-04 | MUST 命令子任务需要审批时**回传用户弹窗**，批准后真正执行，拒绝则如实记录（不再静默跳过） | P0 | 本项目 |
| FR-MA-05 | MUST 支持 `--max-subagents` 并行上限（默认 4），ThreadPoolExecutor 并行派发 | P0 | 001 |
| FR-MA-06 | MUST A2A 协议对接开源 a2a-sdk（`core/a2a/protocol.py`），不自造协议轮子；`InProcessA2AClient` 进程内传输 | P0 | 001 |
| FR-MA-07 | MUST 单个子任务失败不中断整体（`run_task_safe` 兜底），归并结果如实呈现失败 | P0 | 001 |
| FR-MA-08 | MUST 多 Agent 事件并入 `StreamMessage` 流（`MULTI_AGENT_DECOMPOSE` / `MULTI_AGENT_SUBAGENT` / `MULTI_AGENT_DONE`），CLI 与 serve 复用同一消费路径 | P0 | 001 |

### 3.4 企业级安全策略层（`GSagent/core/policy/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-SEC-01 | MUST 提供工作区路径守卫 `PathGuard`：拒绝解析后逃逸出 `workspace_root` 的路径访问（含符号链接逃逸） | P0 | 002 FR-001 |
| FR-SEC-02 | MUST 提供命令守卫 `CommandGuard`：在审批前拦截破坏性命令（删除根/家目录、格式化、fork bomb、关机重启），黑/白名单可配置 | P0 | 002 FR-002 |
| FR-SEC-03 | MUST 支持 HITL 三态审批 `auto`（仅危险工具）/ `always`（全部工具）/ `never`（信任环境免审批），可配置、可运行时 `/hitl` 切换 | P0 | 002 FR-003 |
| FR-SEC-04 | MUST 非交互入口（serve/脚本/SubAgent 无审批界面）且无审批回调时，危险操作拒绝（approver=none），绝不静默放行 | P0 | 002 FR-004 |
| FR-SEC-05 | MUST 全量审计 `AuditLog`：模型 I/O、工具调用、审批决策写不可变 JSONL，含时间戳与 approver/outcome | P0 | 002 FR-005 |
| FR-SEC-06 | MUST 敏感字段（key/token/password/secret/authorization 等）自动脱敏，审计与日志 0 明文 | P0 | 002 FR-006 |
| FR-SEC-07 | MUST 工具分类规则 `guard_kind_for`：界定路径类（PathGuard）与命令类（CommandGuard）工具 | P0 | 002 |

### 3.5 Plan-and-Execute（`GSagent/core/plan/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-PLAN-01 | MUST `--plan` 模式：LLM 产出带依赖关系的子任务 DAG，按拓扑批次并行执行 | P1 | 002 FR-008 |
| FR-PLAN-02 | MUST 失败定位到具体子任务（task_id），依赖失败的后续子任务标记 skipped，如实报告 | P1 | 002 |
| FR-PLAN-03 | MUST plan 模式与多 Agent 编排共享同一套工具/策略/审计 | P1 | 002 |

### 3.6 Runtime API 与后台任务（`GSagent/core/runtime/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-RT-01 | MUST `serve` 提供线程/回合/SSE 事件流 API（`POST /threads`、`POST /threads/{id}/messages`、`GET /threads/{id}/events`） | P2 | 002 FR-011 |
| FR-RT-02 | MUST 提供持久化后台任务队列 `DurableTaskManager`（SQLite），支持投递/查询/取消，按项目目录 scope 隔离 | P2 | 002 FR-009 |
| FR-RT-03 | MUST 任务队列保证原子领取、租约过期重排队（崩溃恢复）、取消后迟到结果不覆盖（canceled 优先） | P2 | 002 FR-010 |
| FR-RT-04 | MUST serve 与 CLI 一样只消费 `StreamMessage` 事件流（宪法 II） | P2 | 002 |

### 3.7 记忆系统（`GSagent/core/memory/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-MEM-01 | MUST 提供长期记忆 `MemoryStore`（SQLite）：跨会话、按 `user + scope` 复合隔离，去重 upsert、LRU 配额清理、离线召回 | P0 | 004 FR-002 |
| FR-MEM-02 | MUST 提供会话记忆 `SessionMemoryStore`（目录）：每轮落盘 `session_history.json` + `meta.json`，按 `<root>/<user>/<session_id>/` 隔离 | P0 | 004 FR-003 |
| FR-MEM-03 | MUST 统一用户身份解析 `resolve_user_key()`：CLI 默认系统登录用户名，可被 `memory.user` 配置 / `AGENT_MEMORY_USER` 覆盖；serve 预留 API 身份接入点 | P0 | 004 FR-001 |
| FR-MEM-04 | MUST 用户 key 防路径穿越与特殊字符（scope key 编码、会话目录安全化） | P0 | 004 FR-007 |
| FR-MEM-05 | MUST 长期记忆工具集（`remember` / `search_memory`）与斜杠命令（`/remember` / `/memory`）透传用户隔离，行为一致 | P1 | 004 FR-005/006 |
| FR-MEM-06 | MUST 向后兼容：调用方不传 user 默认系统用户；既有数据按默认用户归属，零手动迁移 | P0 | 004 FR-004 |

### 3.8 可观测（`GSagent/core/observability/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-OBS-01 | MUST 提供「会话 → 任务 → Agent → 事件」四层对象模型，作为可观测与审计单一事实来源 | P1 | 003 FR-001 |
| FR-OBS-02 | MUST 每任务关联唯一 trace_id；链路由可嵌套 Span（`invoke_agent` / `chat-llm` / `execute_tool`）组成 | P1 | 003 FR-002 |
| FR-OBS-03 | MUST Agent/Task 指标（token/成本/错误数）仅由事件流聚合生成，禁止手工修改路径 | P1 | 003 FR-003 |
| FR-OBS-04 | MUST 完整内容捕获（prompt/completion）默认关闭，仅显式 opt-in（隐私） | P1 | 003 FR-004 |
| FR-OBS-05 | MUST 事件信封携带 A2A 业务 ID（session/task/agent/parent_agent）与 OTel 链路标签（trace/span/parent_span） | P1 | 003 FR-006 |
| FR-OBS-06 | MUST 事件类型覆盖九类生命周期：agent/llm/tool/推理/上下文/异常恢复 + 审批 + A2A 消息收发 + 规划 | P1 | 003 FR-007 |
| FR-OBS-07 | MUST 落地业务模型 ↔ a2a-sdk 协议模型映射（`context_id` ↔ `session_id`、`task_id` 一致、终止态语义一致） | P2 | 003 FR-008 |

### 3.9 评估体系（`GSagent/core/eval/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-EVAL-01 | MUST 支持评估数据集（YAML，`case_id/prompt/expected/tags`）与评估执行 | P1 | 002 FR-013 |
| FR-EVAL-02 | MUST 产出三指标：完成率（normal 组）/ 幻觉率 / 误拒绝率，错误案例回流供专家评审 | P1 | 002 |
| FR-EVAL-03 | MUST 支持基线建立与回归对比（发版无能力退化），`OfflineLLM` 离线回归 / 真实 LLM 联网验收 | P1 | 002 |

### 3.10 快照（`GSagent/core/history/snapshot.py`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-SNAP-01 | MUST 任务执行前后自动创建快照（`--snapshot` 或配置 `policy.snapshot_dir`），滚动保留最近 N 份 | P1 | 002 FR-012 |
| FR-SNAP-02 | MUST 会话快照（history 序列化 + 指纹）可恢复重建会话；工作区快照（文件 hash 清单）可校验变更 | P1 | 002 |

### 3.11 技能与环境信息（`GSagent/core/skills/`）

| ID | 需求 | 优先级 | 来源 |
|---|---|---|---|
| FR-SKILL-01 | MUST 本地技能库：加载/匹配/管理可复用技能（YAML，`name/description/instructions/tool_bindings/keywords`），任务匹配则注入提示词 | P1 | 001 FR-006 |
| FR-SKILL-02 | MUST 环境信息采集：当前 shell/工作目录/平台/Python 版本/可用工具进上下文 | P1 | 001 FR-007 |
| FR-SKILL-03 | MUST 历史/执行日志存储 `HistoryStore`（JSONL），支持按会话/命令查询 | P1 | 001 FR-008 |

---

## 4. 非功能需求

### 4.1 性能

| ID | 需求 | 来源 |
|---|---|---|
| NFR-PERF-01 | 混合任务（≥3 步命令）端到端 5 分钟内完成（SC-001，001） | 001 |
| NFR-PERF-02 | 90% 历史记录查询 2 秒内返回（SC-005，001） | 001 |
| NFR-PERF-03 | 会话→任务→Agent 三层状态快照查询 < 500ms（单机千级事件，SC-002，003） | 003 |
| NFR-PERF-04 | plan 模式无依赖子任务并行执行（SC-005，002） | 002 |

### 4.2 安全

| ID | 需求 | 来源 |
|---|---|---|
| NFR-SEC-01 | 工作区外访问 100% 拒绝；破坏性命令 100% 审批前拦截（SC-001，002） | 002 |
| NFR-SEC-02 | 非交互入口危险操作 0 例静默放行（SC-002，002） | 002 |
| NFR-SEC-03 | 敏感字段 0 例明文落库；任意任务 30 秒内可追溯（SC-003，002） | 002 |
| NFR-SEC-04 | 密钥不落代码，多环境配置隔离（FR-014，002） | 002 |

### 4.3 可靠性 / 可用性

| ID | 需求 | 来源 |
|---|---|---|
| NFR-REL-01 | 后台任务崩溃恢复：worker 被杀后任务 ≤租约期自动重排队；取消后迟到结果 0 例覆盖 canceled（SC-006，002） | 002 |
| NFR-REL-02 | 命令失败时基于上下文自动重试/调整方案，最终回复说明失败与恢复（FR-010，001） | 001 |
| NFR-REL-03 | 会话上下文超限时压缩而非丢弃关键信息（FR-009，001；`SessionCompactor`/`ContextWindowLimiter`） | 001 |

### 4.4 兼容性

| ID | 需求 | 来源 |
|---|---|---|
| NFR-COMP-01 | 自动识别并适配 PowerShell / bash / zsh 三种终端，开箱即用无需手动声明（SC-004，001） | 001 |
| NFR-COMP-02 | 单 Agent 与多 Agent 模式共用同一 `call_stream` 签名，CLI 与 serve 复用事件流消费（宪法 II） | 本项目 |

### 4.5 可维护性 / 工程

| ID | 需求 | 来源 |
|---|---|---|
| NFR-ENG-01 | 依赖可复现（uv.lock 锁定），全新环境 ≤30 分钟复现构建并跑通离线测试（SC-008，002） | 002 |
| NFR-ENG-02 | 代码结构子模块化：`core/` 顶层不堆裸文件，各域归位（tools/ agents/ observability/ history/…） | 本项目 |
| NFR-ENG-03 | 类型注解必须（mypy）；重试走 tenacity；不手写重试循环 | 本项目 |
| NFR-ENG-04 | 新功能需单元测试、新插件需集成测试（宪法 IV）；离线测试用 `ScriptedLLM` 打桩 | 本项目 |

---

## 5. 配置需求

### 5.1 配置来源与优先级

四层覆盖，优先级从低到高：**默认值 → YAML 文件 → 环境变量 → CLI 覆盖**。

- 默认配置文件：**`./.GSagent/config.yaml`**（当前工作目录下，随项目走，不写用户主目录）
- 环境变量：`AGENT_*` 声明式覆盖表（见 5.3）
- CLI 覆盖：`Config.apply_overrides()`（`--api-key` / `--model` / `--max-steps` / `--hitl` 等）

### 5.2 配置段

| 段 | 说明 |
|---|---|
| `llm` | model / api_key / base_url |
| `agent` | max_steps / global_instructions / enable_compaction / compaction_threshold_ratio / compaction_keep_last_n / record_usage |
| `policy` | hitl_mode / workspace_root / command_blacklist / command_allowlist / audit_dir / snapshot_dir / approval_window_sec |
| `runtime` | queue_db / serve_port |
| `cost` | pricing（`{model: {prompt_per_1k, completion_per_1k}}`） |
| `eval` | datasets_dir / baseline_dir |
| `memory` | db_path / sessions_dir / scope / user / max_entries / ttl_days |
| `bash` / `sandbox` | 各工具集配置（allow 白名单 / builtin_allowlist 等） |
| `toolsets` | 启用的工具集列表 |
| `multi_agent` | enabled / max_subagents / orchestrator_model / a2a.transport / sandbox |

### 5.3 关键环境变量（`AGENT_*`）

| 变量 | 对应配置 | 说明 |
|---|---|---|
| `AGENT_MODEL` / `AGENT_API_KEY`（回退 `OPENAI_API_KEY`）/ `AGENT_BASE_URL` | `llm.*` | 模型与凭证 |
| `AGENT_MAX_STEPS` | `agent.max_steps` | 最大迭代步数 |
| `AGENT_MULTI_AGENT` / `AGENT_MAX_SUBAGENTS` | `multi_agent.*` | 多 Agent 开关与上限 |
| `AGENT_HITL_MODE` | `policy.hitl_mode` | auto/always/never |
| `AGENT_WORKSPACE_ROOT` | `policy.workspace_root` | 工作区根 |
| `AGENT_RECORD_USAGE` | `agent.record_usage` | 是否记录用量/成本 |
| `AGENT_SERVE_PORT` | `runtime.serve_port` | serve 端口 |
| `AGENT_MEMORY_DB` / `AGENT_MEMORY_SESSIONS_DIR` / `AGENT_MEMORY_SCOPE` / `AGENT_MEMORY_USER` | `memory.*` | 记忆系统 |
| `AGENT_LOG_LEVEL` / `AGENT_LOG_FILE` / `AGENT_LOG_DIR` / `AGENT_LOG_THIRD_PARTY_LEVEL` | 日志（`common/env_vars.py`） | 日志级别/开关/目录 |
| `AGENT_BASH_PATH` | bash 工具集 | 显式 bash 路径 |
| `AGENT_BASH_UNSAFE_ARGS_MODE` | bash 工具集 | 危险参数处理模式 |

---

## 6. 数据需求

### 6.1 数据根目录（本项目新规范）

**所有默认数据落在当前工作目录 `./.GSagent/`**（单一来源 `GSagent/common/paths.py`），不写用户主目录 `~/.agent`：

| 子路径 | 内容 |
|---|---|
| `./.GSagent/config.yaml` | 配置文件 |
| `./.GSagent/logs/agent.log` | 运行日志（5MB×5 轮转，UTF-8） |
| `./.GSagent/audit/audit.jsonl` | 审计留痕 |
| `./.GSagent/snapshots/` | 会话/工作区快照 |
| `./.GSagent/memory.db` | 长期记忆 SQLite |
| `./.GSagent/memories/sessions/` | 会话记忆目录（按 user/session） |
| `./.GSagent/runtime.db` | 后台任务队列 SQLite |
| `./.GSagent/history.jsonl` | 执行历史 |
| `./.GSagent/skills/` | 本地技能库 |
| `./.GSagent/bash_approved_prefixes.yaml` | CLI 免批前缀 |

> 覆盖手段：各路径可用对应配置段 / `AGENT_*` 环境变量 / CLI `--config` 显式指定。从别的目录启动时注意落在该目录的 `.GSagent/`。

### 6.2 数据格式

| 数据 | 格式 |
|---|---|
| 审计 | JSONL（每行一事件，`{ts, session_id, type, payload, outcome, approver, cwd, usage}`） |
| 历史 | JSONL（`HistoryRecord` 序列化） |
| 任务队列 | SQLite（tasks 表：id/scope/state/payload/lease_owner/lease_until/…） |
| 长期记忆 | SQLite（`UNIQUE(user, scope, content_hash)`） |
| 技能 | YAML |
| 快照 | JSON（`{snapshot_id, kind, ts, ...}`） |
| 评估 | YAML 数据集 / JSON 基线 |

---

## 7. 本次会话确认的增量需求

以下为本次工作会话中用户明确提出并已落地的需求：

| ID | 需求 | 状态 |
|---|---|---|
| IN-01 | 数据完全本地化：配置/日志/审计/快照/记忆/队列/历史/技能/免批前缀全部落在 `./.GSagent/`，不写用户目录 | ✅ 已实现 |
| IN-02 | 命令行入口可用：`agent` 命令可跑（已重装刷新 entry point）；`python -m GSagent` 不支持（无 `__main__.py`，保留单一入口） | ✅ 已实现 |
| IN-03 | 代码结构清晰：`core/` 顶层 4 个裸文件归位（ToolExecutor→tools/、ToolCallingLLM→agents/、CostEstimator→observability/、SnapshotManager→history/），`ToolCallingLLM` 去冗余（抽 `AuditUsageMixin`、删死代码、修 `_parse_tc_arguments` NameError bug） | ✅ 已实现 |
| IN-04 | 多 Agent 命令子任务审批回传：需要审批时暂停回传用户弹窗（两阶段编排），批准后真正执行、拒绝如实记录，不再静默跳过 | ✅ 已实现 |
| IN-05 | 命令解析稳健：`powershell`/`pwsh` 等包装命令可识别；编排 prompt 明确禁止伪命令与外壳包装 | ✅ 已实现 |

---

## 8. 未完成项与待办

| ID | 事项 | 优先级 | 说明 |
|---|---|---|---|
| TODO-01 | 命令子任务解析兜底：首词不在命令表时回退交给 sandbox 执行（让 `Get-ComputerInfo` 等不常见命令可执行，伪命令明确报错） | P1 | 已提议，待确认 |
| TODO-02 | `command_prefixes_for` 引号路径误切：`'E:\new_agent'` 被误切成 `ew_agent'` 作建议前缀，影响"记住前缀"白名单 | P2 | 待修 |
| TODO-03 | 旧 `~/.agent/config.yaml` 清理（确认迁移无误后可选删除） | P3 | 待用户确认 |
| TODO-04 | `serve` 模式记忆用户身份接入（API 身份，`resolve_user_key` 预留接口） | P3 | 004 后续增量 |
| TODO-05 | 分布式可观测（移除事件列表、外部时序存储） | P3 | 003 后续增量 |
| TODO-06 | 容器化沙箱（v2） | P3 | 002 后续增强 |

---

## 9. 验收标准与成功指标

### 9.1 多 Agent 与安全（001 / 002）

| ID | 指标 | 目标 |
|---|---|---|
| SC-001 | 混合任务（≥3 步命令）一次性完成成功率 | ≥ 80% |
| SC-002 | 高风险操作提交前确认/拦截 | 100%（0 例未授权执行） |
| SC-003 | 三终端（PowerShell/bash/zsh）开箱即用 | 100% |
| SC-004 | 历史查询 2 秒内返回 | ≥ 90% |
| SC-005 | 工作区外访问 / 破坏性命令拦截 | 100% / 100% |
| SC-006 | 非交互入口危险操作静默放行 | 0 例 |
| SC-007 | 审计覆盖模型调用与工具执行、敏感字段明文 | 100% / 0 例 |
| SC-008 | 后台任务崩溃恢复重排队 / 取消后迟到结果覆盖 | 100% / 0 例 |

### 9.2 评估（002）

| ID | 指标 | 目标 |
|---|---|---|
| SC-EVAL-01 | 评估集真实业务案例数 | ≥ 100 条 |
| SC-EVAL-02 | 完成率 / 幻觉率 / 误拒绝率 | ≥ 80% / ≤ 5% / ≤ 10% |
| SC-EVAL-03 | 发版回归 | 无能力退化 |

### 9.3 记忆隔离（004）

| ID | 指标 | 目标 |
|---|---|---|
| SC-MEM-01 | 同一 scope 两用户记忆互不可见 | 100% |
| SC-MEM-02 | 用户 A 清理不影响用户 B | 影响率 0% |
| SC-MEM-03 | 两用户相同 session_id 互不覆盖 | 冲突率 0 |
| SC-MEM-04 | 既有单用户数据零手动迁移可读 | 100% |

### 9.4 可观测（003）

| ID | 指标 | 目标 |
|---|---|---|
| SC-OBS-01 | 已执行任务经 task_id 完整回溯链路（含失败定位） | 回溯率 100% |
| SC-OBS-02 | 三层状态快照查询耗时 | < 500ms |
| SC-OBS-03 | 指标与事件流聚合一致（无绕过手工写入口） | 100% |
| SC-OBS-04 | 隐私开关关闭时完整内容泄漏 | 0 |

---

*本文档由 `specs/` 四个 feature 契约 + 本次工作会话需求综合整理；实现细节以代码与各 spec 契约为准。*
