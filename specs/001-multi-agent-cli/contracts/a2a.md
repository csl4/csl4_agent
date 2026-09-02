# A2A 协议契约: 多Agent CLI 框架

**Date**: 2026-09-02 | **Phase**: 1（/speckit-plan 输出）

> 采用开源 A2A（Agent2Agent）协议模型 + a2a-sdk（见 research.md 决策 1）。
> v1 以进程内方式使用协议模型；HTTP/SSE 端点为可选扩展（`server.py`），不阻塞 v1。

## AgentCard（Agent 自描述）

每个 Agent 角色暴露 AgentCard，供主 Agent 发现与路由：

```jsonc
{
  "name": "orchestrator",
  "description": "负责任务拆解与并行调度",
  "url": "in-process://orchestrator",
  "capabilities": {
    "tasks": { "streaming": true },
    "skills": []
  },
  "defaultInputModes": ["text", "data"],
  "defaultOutputModes": ["text", "data"]
}
```

## Task 生命周期

A2A 标准状态机（FR-001 编排语义）：

```text
submitted → working ⇄ input-required → completed | failed | canceled
```

- 主 Agent 将用户任务拆为子任务，通过 `Task` 分发给编排/业务 Agent。
- 编排 Agent 可为并行子任务创建多个 SubAgent，各 SubAgent 独立 `Task`。
- `input-required` 用于需要用户确认（FR-003）或补充信息的暂停态。

## 消息类型（Part）

Agent 间内容交换（FR-005 结果整理）：

| Part 类型 | 用途 | 示例 |
|-----------|------|------|
| `text` | 自然语言内容 | 任务说明、回复片段 |
| `file` | 文件引用/产物 | 生成的报告路径 |
| `data` | 结构化数据 | 命令执行结果（JSON） |

## 错误与取消

- Agent 调用失败 → 返回 `failed` Task 状态 + 错误消息；重试由 `tenacity` 处理（宪法 V）。
- 用户中断 → 传播 `canceled` 至所有相关 Task，清理已启动进程（Edge Case）。
- 网络/进程内传输失败 → 上层收到明确错误，不静默吞掉（FR-004 语义）。

## 验证

- A2A 消息/Task 序列化往返一致（unit 测试：构造→序列化→反序列化→断言字段）。
- 集成测试（`responses` mock HTTP）：主 Agent 分发→工作 Agent 回执→结果归并。
- TODO(A2A_VERIFY): 与 a2a-sdk 实际类型对齐字段名（research.md follow-up）。
