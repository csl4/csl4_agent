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
| CLI 交互 | `chat`（含 `--multi-agent`）/ `run` / `serve`（占位）/ `toolset` / `agents list` / `skills list\|add\|rm` / `history session\|command` / `version` |
| 工具执行 | 插件化 `Toolset` / `Tool`，审批流 + 白名单 + 敏感路径拦截 |
| 上下文管控 | 超限压缩（`SessionCompactor`）、token 阈值体检 |
| 多Agent 编排 | 主 / 编排 / 业务 / 动态 SubAgent；v1 进程内 A2A transport，基于 `a2a-sdk` protobuf 类型（`agent/core/a2a/`） |
| 终端适配 | PowerShell / bash / zsh 探测与命令改写 |
| 轻量沙箱 | 子进程隔离 + 超时 + 受控工作目录 |
| 技能注入 | 本地技能库按任务自动匹配，注入提示词 |
| 历史记录 | 命令执行 / 会话生命周期 JSONL 落库与查询 |

## 文档

- [系统架构](docs/architecture.md)
- [多Agent 编排](docs/multi-agent.md)
- [实现笔记与踩坑](docs/implementation-notes.md)

## 测试

```bash
python -m pytest tests -m "not llm"   # 离线单测 + 集成测试
```

## 配置

- 配置文件：`~/.agent/config.yaml`
- 关键环境变量：`AGENT_API_KEY`（回退 `OPENAI_API_KEY`）、`AGENT_MODEL`、`AGENT_BASE_URL`、`AGENT_MAX_STEPS`、`AGENT_MULTI_AGENT`、`AGENT_MAX_SUBAGENTS`
