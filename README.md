# Agent

通用 LLM Agent 框架（Python 3.10+），CLI 交互 + 工具调用 + 多Agent 编排。

## 安装

```bash
uv pip install -e ".[dev]"   # 装进当前激活环境（conda base_llm），含开发/测试依赖
```

## 快速开始

```bash
agent chat                 # 单 Agent 交互
agent chat --multi-agent   # 多Agent 编排模式
```

## 功能

| 功能 | 说明 |
|---|---|
| CLI 交互 | `chat`（含 `--multi-agent`）/ `run` / `serve` / `toolset` / `agents list` / `skills list\|add\|rm` / `history session\|usage` / `tasks list\|cancel` / `snapshot list\|restore` / `eval run\|baseline\|list` / `version`；chat 内斜杠命令 `/exit` `/hitl` `/memory` `/tool` `/skill` `/remember` |
| 工具执行 | 插件化 `Toolset` / `Tool`，审批流 + 白名单 + 敏感路径拦截 |
| 企业级安全策略 | 统一策略层（`GSagent/core/policy/`）：PathGuard / CommandGuard / AuditLog / HITL 三态（auto/always/never），工作区外与破坏性命令审批前拦截；输入/输出侧 Guardrail（`input_guard.py` / `output_guard.py`，注入/违规拦截 + 结构化输出校验） |
| 审计与用量 | 审计 JSONL 全量留痕（密钥脱敏）+ token 消耗与本地定价成本估算（`history usage`） |
| 上下文管控 | 超限压缩（`SessionCompactor`）、token 阈值体检 |
| 多Agent 编排 | 主 / 编排 / 业务 / 动态 SubAgent；v1 进程内 A2A transport，基于 `a2a-sdk` protobuf 类型（`GSagent/core/a2a/`） |
| Plan-and-Execute | `chat --plan`：LLM 产出任务 DAG，拓扑批次并行执行，失败定位（`GSagent/core/plan/`） |
| 持久化任务 + Runtime API | `serve`（FastAPI：线程/回合/SSE）+ SQLite 持久化任务队列（原子租约、取消保护、崩溃恢复，`GSagent/core/runtime/`） |
| 快照 | 任务执行前后自动快照（会话/工作区清单），滚动保留与恢复（`GSagent/core/snapshot.py`） |
| 评估体系 | 三指标（完成率/幻觉率/误拒绝率）+ 基线回归对比 + 错误案例回流（`GSagent/core/eval/`） |
| 终端适配 | PowerShell / bash / zsh 探测与命令改写 |
| 轻量沙箱 | 子进程隔离 + 超时 + 受控工作目录 |
| 技能注入 | 本地技能库按任务自动匹配，注入提示词 |
| 记忆系统 | 长期记忆（SQLite `memory_store`，跨会话按项目 scope，agent 工具 `remember`/`search_memory` + 手动 `/remember`）+ 会话记忆目录（`session_history` 每轮落盘） |
| LangGraph 编排 | 显式图编排主循环（`GSagent/core/orchestration/`：state/graph/nodes），外部 `call_stream` 契约不变；审批/前端暂停可接续、步数熔断、死循环检测 |
| 可观测性 | OpenTelemetry 接入（OpenLLMetry 自动埋点 + 手动业务 span，GenAI 语义约定，token/费用/链路贯穿）+ Append-Only 事件流（`AgentEventEnvelope` → 指标聚合），默认关闭零开销（见 [docs/observability.md](docs/observability.md)） |
| 历史记录 | 命令执行 / 会话生命周期 JSONL 落库与查询 |

## 文档

- [系统架构](docs/architecture.md)
- [多Agent 编排](docs/multi-agent.md)
- [企业级能力](docs/enterprise.md)
- [记忆系统](docs/memory.md)
- [实现笔记与踩坑](docs/implementation-notes.md)

## 测试

```bash
python -m pytest tests -m "not llm"   # 离线单测 + 集成测试
```

## 配置

- 配置文件：`./.GSagent/config.yaml`
- 关键环境变量：`AGENT_API_KEY`（回退 `OPENAI_API_KEY`）、`AGENT_MODEL`、`AGENT_BASE_URL`、`AGENT_MAX_STEPS`、`AGENT_MULTI_AGENT`、`AGENT_MAX_SUBAGENTS`、`AGENT_MEMORY_DB`、`AGENT_MEMORY_SESSIONS_DIR`、`AGENT_MEMORY_SCOPE`
