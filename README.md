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
| 工具执行 | langchain `@tool` 工具集（`ToolRegistry` + 守卫/审批包装），命令前缀校验 + 敏感路径拦截 + 动态审批（`interrupt()` 人在回环） |
| 企业级安全策略 | 统一策略层（`GSagent/core/policy/`）：PathGuard / CommandGuard / AuditLog / HITL 三态（auto/always/never），工作区外与破坏性命令审批前拦截；输入/输出侧 Guardrail（`input_guard.py` / `output_guard.py`，注入/违规拦截 + 结构化输出校验） |
| 审计与用量 | 审计 JSONL 全量留痕（密钥脱敏）+ token 消耗与本地定价成本估算（`history usage`） |
| 上下文管控 | 超限压缩（`SessionCompactor`）、token 阈值体检 |
| 多Agent 编排 | `chat --multi-agent`：纯 LangGraph 主编排图（`GSagent/core/orchestration/multi.py`）——decompose 拆解 → Send 原生并行 → `create_agent` worker 子图（业务/命令）→ finalize 归纳；审批 interrupt 自动冒泡父图、per-interrupt-id 恢复 |
| Plan-and-Execute | `chat --plan`：纯 LangGraph Plan 图（`GSagent/core/orchestration/plan.py`）——LLM 产出任务 DAG（`core/plan/planner.py`），按依赖批次 Send 并行执行，失败定位 |
| 持久化任务 + Runtime API | `serve`（FastAPI：线程/回合/SSE）+ SQLite 持久化任务队列（原子租约、取消保护、崩溃恢复，`GSagent/core/runtime/`） |
| 快照 | 任务执行前后自动快照（会话/工作区清单），滚动保留与恢复（`GSagent/core/snapshot.py`） |
| 评估体系 | 三指标（完成率/幻觉率/误拒绝率）+ 基线回归对比 + 错误案例回流（`GSagent/core/eval/`） |
| 终端适配 | PowerShell / bash / zsh 探测与命令改写 |
| 轻量沙箱 | 子进程隔离 + 超时 + 受控工作目录 |
| 技能注入 | 本地技能库按任务自动匹配，注入提示词 |
| 记忆系统 | 长期记忆业务语义（`StoreMemoryAdapter` over langgraph BaseStore：namespace=(user,scope)、content_hash 去重、离线打分召回、LRU 配额，`GSagent/core/memory/langgraph_store.py`）+ SqliteSaver/SqliteStore 持久化（`core/memory/saver.py`）+ 会话记忆目录（`session_history` 每轮落盘） |
| LangGraph 编排 | 纯 langgraph 执行：单 Agent 图（guard_in/agent/ToolNode/guard_out）、审批下沉进 `@tool`（`interrupt()` 人在回环）、SqliteSaver 会话/断点持久化、步数熔断、死循环检测；CLI/serve 只消费 `GraphAgent.stream()`（StreamMessage + PauseRequest，per-interrupt-id resume） |
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
