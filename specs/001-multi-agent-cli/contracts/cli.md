# CLI 契约: 多Agent CLI 框架

**Date**: 2026-09-02 | **Phase**: 1（/speckit-plan 输出）

> CLI 只消费 `StreamMessage` 事件流，不接触底层 Tool/Toolset（宪法 II）。
> 本契约为现有 CLI（`agent run` / `agent chat`）的**增量扩展**，不改变既有命令行为。

## 新增命令（增量）

### `agent chat`（多Agent 模式标志）

现有交互命令扩展多Agent 编排开关：

```text
agent chat [--multi-agent] [--max-subagents N] [OPTIONS]
```

| 参数 | 说明 |
|------|------|
| `--multi-agent` | 启用四角色多Agent 编排（主→编排→业务→SubAgent） |
| `--max-subagents N` | 单任务最大并行 SubAgent 数（默认 4） |
| 其余 | 与现有 `chat` 一致（model、max-steps 等） |

### `agent agents list`

列出当前可用的 Agent 角色与能力（A2A AgentCard 本地视图）：

```text
agent agents list
```

输出每个角色的：名称、角色类型（main/orchestrator/business/subagent）、能力、状态。

### `agent skills <list|add|rm>`（P3）

技能库管理：

```text
agent skills list
agent skills add <skill.yaml>
agent skills rm <skill-name>
```

### `agent history <query>`（P3）

查询执行日志/会话历史：

```text
agent history session <session-id>
agent history command <pattern>
```

## 事件契约（不变）

- CLI 依旧消费 `StreamMessage`（`agent/utils/stream.py` 的 `StreamEvents`）事件流。
- 多Agent 编排产生的中间事件（任务拆解、SubAgent 启停）以新增事件类型追加，不破坏既有事件。

## 验证

- 增量命令可用 `agent --help` / `agent <sub> --help` 校验参数契约。
- 现有 `agent run` / `agent chat` 不带 `--multi-agent` 时行为与旧版一致（向后兼容）。
