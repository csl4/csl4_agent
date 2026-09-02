# 配置契约: 多Agent CLI 框架

**Date**: 2026-09-02 | **Phase**: 1（/speckit-plan 输出）

> 宪法 III：配置向后兼容——新增字段不改名既有字段；重命名时用 Pydantic
> `extra="allow"` + `model_validator` 映射旧名，不留废弃字段。

## 新增配置段 `multi_agent`（`~/.agent/config.yaml`）

```yaml
multi_agent:
  enabled: false                # 默认关闭，保持单Agent 行为兼容
  max_subagents: 4              # 单任务最大并行 SubAgent 数
  orchestrator_model: ""        # 空则复用 llm.model
  a2a:
    transport: in-process       # v1: in-process；后续可 http
    # server_url: ""            # transport=http 时生效（可选）
  sandbox:
    type: lightweight           # v1: lightweight；后续 container
    timeout_seconds: 30
    working_dir: ""
```

## 环境变量（可选覆盖）

| 变量 | 覆盖 |
|------|------|
| `AGENT_MULTI_AGENT` | `multi_agent.enabled`（`1/0`） |
| `AGENT_MAX_SUBAGENTS` | `multi_agent.max_subagents` |

## 兼容性规则

- 不提供 `multi_agent` 段或 `enabled: false` → 行为与旧版完全一致。
- 若未来 `sandbox.type` 需改名，走 `extra="allow"` + `model_validator` 映射，不删旧名。
- 未知字段按 Pydantic 忽略策略处理（`extra="allow"`），不抛错。

## 验证

- 无配置 → 默认加载成功，不破坏现有 `agent run` / `agent chat`。
- 旧版 config.yaml（无 multi_agent 段）加载零告警、行为不变。
- `enabled: true` → `agent chat` 进入多Agent 编排。
