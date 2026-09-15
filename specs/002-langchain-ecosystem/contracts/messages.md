# Contract: BaseMessage 消息转换

> 来源：`spec.md` FR-001/008 ｜ 设计：`research.md` R-01/R-05 ｜ 数据模型：`data-model.md` §1/§4

> **后续简化（拆壳）**：外部消费方已不再用 `StreamMessage` 对象——渲染事件改经 langgraph custom 流（`{"type","data"}` dict），SSE 序列化收敛为 `event_to_sse()`（`GSagent/utils/stream.py`）。本契约的 dict↔BaseMessage 转换层（`GSagent/core/llm_adapter.py`）仍有效。

## 目的

定义编排内部 `BaseMessage` 世界与既有外部消费方（StreamMessage SSE 事件、审计、截断）的转换契约。**外部输出形态不变**（FR-007/008）。

## 1. 内部消息类型（langchain）

| 角色 | 类型 | 说明 |
|---|---|---|
| 用户/系统 | `HumanMessage` / `SystemMessage` | `build_chat_messages` 产出 |
| 模型 | `AIMessage` | `content` + `tool_calls`（name/args/id） |
| 工具 | `ToolMessage` | `content` + `tool_call_id`（`add_messages` 配对） |

状态合并：`GraphState.messages: Annotated[list[BaseMessage], add_messages]`（追加式，工具调用与其结果自动配对）。

## 2. 转换函数（`GSagent/core/llm_adapter.py`）

- `dict_to_messages(dicts) -> list[BaseMessage]`：OpenAI dict → BaseMessage（外部输入兼容）。
- `messages_to_dict(messages) -> list[dict]`：BaseMessage → OpenAI dict（StreamMessage 事件快照、审计 payload、既有消费方）。
- `extract_usage(aimessage) -> ContextWindowUsage`：`response_metadata["token_usage"]` → 既有用量对象。

**转换规则**：
- 不丢失 `tool_call_id` / `tool_calls` / `name` 字段（双向保真）。
- `content` 支持 str 与多模态 list（对齐 `message_text` 提取语义）。
- 未知/扩展字段经 `additional_kwargs` 保留（不静默丢弃）。

## 3. 消费方适配

| 消费方 | 适配 |
|---|---|
| `StreamMessage.ANSWER_END` data | `content` 直取；`messages` 快照经 `messages_to_dict()` |
| `_audit_model_call` | `extract_usage` → 既有 `ContextWindowUsage` → 审计 payload |
| `SessionCompactor` / `ContextWindowLimiter` | 内部消息改 BaseMessage（或经转换） |
| 事件发射 `LLM_RESPONSE` | `tokens_in/out` 从 `extract_usage` |

## 4. 验收要点

- 同一轮对话，`dict_to_messages` 后经 `messages_to_dict` 双向一致（字段无损）。
- SSE 事件 `ANSWER_END.messages` 快照结构与迁移前一致（前端零改动）。
- 审计/事件 token 数值与 `AIMessage.response_metadata["token_usage"]` 一致（SC-005）。
