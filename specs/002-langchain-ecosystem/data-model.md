# Data Model: 完全迁移到 langchain/langgraph 生态

> 阶段：Phase 1（/speckit-plan）｜ 日期：2026-09-13
> 对应 spec：`spec.md` ｜ 设计：`research.md` R-01~R-07

---

## 1. 编排图状态（GraphState）—— BaseMessage 化

消息字段从「OpenAI dict 列表」改为「langchain `BaseMessage` 列表 + `add_messages` reducer」：

```python
# GSagent/core/orchestration/state.py（设计草案）
from typing import Annotated, Any, Dict, List, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict, total=False):
    # 对话消息：BaseMessage 列表，add_messages 追加式合并（Human/AI/Tool 自动配对）
    messages: Annotated[List[BaseMessage], add_messages]

    # 工具调用（tools 节点消费；从 AIMessage.tool_calls 提取）
    last_tool_calls: List[Dict[str, Any]]
    prev_tool_calls: List[Dict[str, Any]]  # 死循环检测
    no_progress_streak: int

    # 编排元信息
    iteration: int
    tool_number: int
    terminated: Optional[str]  # max_steps | cancelled | blocked

    # 回合级配置
    enable_tool_approval: bool
    request_context: Optional[Dict[str, Any]]
    cancel_event: Optional[Any]

    # 事件缓冲（append reducer；编排内部）
    _events: Annotated[List[Dict[str, Any]], _append_list]
    _stream_messages: Annotated[List[Dict[str, Any]], _append_list]
```

**消息语义**：
- `HumanMessage(content=user_input)`：用户输入（含 system 提示由 `build_chat_messages` 输出 `SystemMessage` + `HumanMessage`）。
- `AIMessage(content, tool_calls=[{"name","args","id"}])`：模型响应；`tool_calls` 为空即回答完成。
- `ToolMessage(content, tool_call_id=...)`：工具结果，`add_messages` 按 `tool_call_id` 配对。
- `SystemMessage(content=...)`：系统提示。

**转换点（`llm_adapter.py`）**：
- `dict_to_messages(list[dict]) -> list[BaseMessage]`：OpenAI dict → BaseMessage（外部输入兼容）。
- `messages_to_dict(list[BaseMessage]) -> list[dict]`：BaseMessage → OpenAI dict（供 StreamMessage 事件快照、既有消费方）。
- `extract_usage(aimessage) -> ContextWindowUsage`：`response_metadata["token_usage"]` → 既有用量对象。

---

## 2. 工具注册与包装层

### @tool 注册表（`tools/registry.py`）

```python
# 注册表：替代 BUILTIN_PYTHON_TOOLSETS
_TOOLS: Dict[str, BaseTool] = {}

def register_tool(fn) -> BaseTool: ...      # @tool(fn) 并登记
def get_all_tools() -> list[BaseTool]: ...  # 全量工具（供 bind_tools / ToolNode）
```

### 守卫/审批包装层

```python
def wrap_with_guards(tool: BaseTool, guards) -> BaseTool:
    """命令/路径守卫：调用前按 rules.guard_kind_for 分类拦截 → 错误 + 审计 blocked。"""

def wrap_with_approval(tool: BaseTool, hitl, loop) -> BaseTool:
    """HITL 审批：高风险工具返回审批信号 → 编排 tools 节点 interrupt() 暂停。"""

def build_tool_node(tools, loop) -> ToolNode:
    """ToolNode 装配（handle_tool_errors 兜底；V-02 验证审批同批语义）。"""
```

**守卫/审批状态流**（保持既有行为）：
- `PathGuard`/`CommandGuard`：`params["path"]`/`params["command"]` 校验，拦截 → `ToolMessage(content="blocked...")` + `AuditLog(blocked)`。
- HITL（auto/always/never）：审批信号 → tools 节点收集 → `interrupt()` → 恢复 `Command(resume)` → 重新执行（`user_approved=True`）/ 拒绝（错误 ToolMessage）。
- 工具配置：原 `config.yaml` 的 `bash:`/`sandbox:` 等段经注册表工厂注入（闭包捕获）。

---

## 3. LLM 装配（`providers/factory.py`）

```python
def create_chat_model(config: dict, tools: Optional[list] = None) -> BaseChatModel:
    """ChatOpenAI 装配：model/api_key/base_url（OpenAI 兼容网关）+ bind_tools。"""
```

**配置映射**（`llm` 段 → ChatOpenAI）：
| 配置 | ChatOpenAI 参数 |
|---|---|
| `llm.model` | `model` |
| `llm.api_key` | `api_key` |
| `llm.base_url` | `base_url`（OpenAI 兼容端点） |
| `agent.max_steps` | 编排层熔断（不变） |

**usage/上下文**：
- `extract_usage(aimessage)` → `ContextWindowUsage(prompt_tokens, completion_tokens, total_tokens, cache_*, reasoning_tokens)`（`response_metadata["token_usage"]`）。
- `get_num_tokens_from_messages(messages)` → 截断/压缩判定（替代 `count_tokens`）。
- 上下文窗口：模型默认表（`get_model_context_window` 映射）。

---

## 4. 审计 / 事件流适配

| 消费方 | 适配（经 `llm_adapter`） |
|---|---|
| `_audit_model_call` | token 从 `AIMessage.response_metadata["token_usage"]` → `ContextWindowUsage` → 既有审计 payload |
| `AgentEventEnvelope.LLM_RESPONSE` | `tokens_in/out` 映射 + `CostEstimator.estimate` |
| `StreamMessage.ANSWER_END` | `content` 从 `AIMessage.content`；`messages` 快照经 `messages_to_dict()` |
| `START_TOOL`/`TOOL_RESULT` | `AIMessage.tool_calls` → 事件；`ToolMessage` → 事件 |
| 截断/压缩 | `SessionCompactor`/`ContextWindowLimiter` 内部改 `messages_to_dict()` 或直接 BaseMessage |

**输出形态不变**：SSE 事件、审计 JSONL、事件信封对消费方零改动。

---

## 5. 实体关系总览

```text
GraphState.messages: BaseMessage[]  ← add_messages 追加合并
  ├─ SystemMessage / HumanMessage   ← build_chat_messages（prompts 适配）
  ├─ AIMessage(tool_calls)          ← ChatOpenAI.bind_tools().invoke()
  └─ ToolMessage(tool_call_id)      ← ToolNode / 工具执行
@tool 注册表 → build_tool_node() → ToolNode → 守卫/审批包装层 → 执行
create_chat_model(config) → ChatOpenAI（base_url=OpenAI 兼容网关）
llm_adapter：dict_to_messages / messages_to_dict / extract_usage（单一适配点）
```
