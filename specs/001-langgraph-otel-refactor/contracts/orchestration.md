# Contract: 编排层（LangGraph）

> 来源：`spec.md` FR-003/004/005/011/013 ｜ 设计：`research.md` R-02/R-03 ｜ 数据模型：`data-model.md` §1

## 目的

定义 LangGraph 编排层对外的**稳定契约**：`ToolCallingLLM` 外壳接口不变，编排图内部实现，两者解耦。消费方（CLI / serve / 多 Agent / Plan 模式）零改动。

## 1. 外部接口契约（不变，禁止破坏）

| 接口 | 签名 | 消费方 |
|---|---|---|
| `ToolCallingLLM.call_stream` | `(messages, enable_tool_approval=False, tool_decisions=None, frontend_tool_results=None, request_context=None, cancel_event=None, tool_number_offset=0, iteration_offset=0) -> Generator[StreamMessage, None, None]` | `main.py` / `main_agent.py` / `runtime/server.py` |
| `ToolCallingLLM.run_task` | `(task: Task) -> Task` | A2A 无头入口（`main_agent.py`） |
| `ToolCallingLLM.set_hitl_mode` | `(mode: str) -> None` | `/hitl` 运行时切换 |
| `Config.create_tool_calling_llm` | 现有签名 | 全部装配点 |

**约束**：
- `call_stream` 仍逐个 yield `StreamMessage`（SSE 契约，`utils/stream.py` 枚举不变）。
- 暂停/恢复语义不变：`tool_decisions` / `frontend_tool_results` 作为**下次调用**的接续参数（R-03，checkpointer 状态重入，而非 `interrupt()`/`Command`）。
- `cancel_event` 检查保留：设置即产出 `ERROR`（cancelled by user）并终止。

## 2. 编排图契约（内部）

```text
START ─▶ guard_in ─▶ agent ─▶ should_continue ─┬─ tools ─▶ compact? ─▶ agent
   │                 (LLM)        │ (无 tool_calls)         │
   │                               ▼                        │
   │                            guard_out ─▶ END            │
   └───────────── 暂停（approval/frontend）──────────────────┘
```

| 节点 | 职责 | 事件（StreamEvents） | 事件信封（AgentEventType） |
|---|---|---|---|
| `guard_in` | 输入侧 Guardrail（注入/合规） | 拦截时 `ERROR` | `STATE_UPDATE` / `FALLBACK_TRIGGERED` |
| `agent` | 调 LLM（含流式 delta） | `ANSWER_DELTA` / `USAGE` / `ANSWER_END` | `LLM_REQUEST/RESPONSE` |
| `should_continue` | 条件边：无 tool_calls → 出口；有 → tools | — | `REASONING` / `STATE_UPDATE` |
| `tools` | 并行执行工具（ThreadPool 或 langgraph parallel） | `START_TOOL` / `TOOL_RESULT` / `APPROVAL_REQUIRED` / `FRONTEND_PAUSE` | `TOOL_DECISION/CALL_START/CALL_END/ERROR` |
| `compact` | 上下文压缩（复用 `SessionCompactor`/`ContextWindowLimiter`） | `COMPACTION_START` / `COMPACTED` | `CONTEXT_PRUNED` |
| `guard_out` | 输出侧 Guardrail（结构化校验/敏感过滤） | 失败降级标记 | `FALLBACK_TRIGGERED` |
| `checkpoint`（checkpointer） | 每 super-step 持久化状态 | — | — |

**契约规则**：
1. 每个节点执行期间产生的事件先写入 `GraphState._events` / `_stream_messages` 缓冲，super-step 边界由 `call_stream` 适配器 flush 为 `StreamMessage`（保持流式与可打断）。
2. `tools` 节点内 `APPROVAL_REQUIRED`/`FRONTEND_PAUSE` → 设置 `pause` 字段，图提前返回；**兄弟调用同批全部结算**（现有"drain all futures"语义，防 tool_call_id 孤儿，`tool_calling_llm.py` L336 注释）。
3. 步数熔断：`iteration >= max_steps` 时强制走 `guard_out`/`END` 并标记 `terminated="max_steps"`，返回部分结果（宪法 2.3）。
4. 死循环检测：`last_tool_calls` 连续 3 步相同 → 终止（宪法 10.1 / 附录 A）。

## 3. 验收要点

- 对同一输入，LangGraph 版与既有手写版在**非暂停路径**产出完全一致的 `StreamMessage` 序列（回归对照）。
- 审批暂停 → 下次以 `tool_decisions` 接续 → 恢复后继续执行且不重复已完成节点（SC-005）。
- `max_steps` 熔断时返回部分结果并带 `max_steps_reached=True`（现状语义）。
