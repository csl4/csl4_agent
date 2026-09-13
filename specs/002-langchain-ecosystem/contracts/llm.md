# Contract: ChatOpenAI 装配（LLM 层）

> 来源：`spec.md` FR-002/011 ｜ 设计：`research.md` R-02 ｜ 数据模型：`data-model.md` §3

## 目的

定义 `LiteLLMProvider` → `langchain-openai` `ChatOpenAI` 的装配与 usage 契约。配置语义保留（`AGENT_MODEL`/`AGENT_BASE_URL`/`AGENT_API_KEY`），token/费用可观测仍准确（SC-005）。

## 1. 装配（`GSagent/core/providers/factory.py`）

```python
def create_chat_model(config: dict, tools: Optional[list] = None) -> BaseChatModel:
    """ChatOpenAI 装配：model/api_key/base_url（OpenAI 兼容网关）+ bind_tools。"""
```

| 配置 | ChatOpenAI 参数 |
|---|---|
| `llm.model` | `model` |
| `llm.api_key` | `api_key` |
| `llm.base_url` | `base_url`（OpenAI 兼容端点，deepseek 等） |
| `tools`（可选） | `bind_tools(tools)`（tool calling） |

## 2. usage / 上下文

- **usage 提取**（`llm_adapter.extract_usage`）：`aimessage.response_metadata["token_usage"]` → `ContextWindowUsage(prompt_tokens, completion_tokens, total_tokens)`（cache/reasoning 按供应商字段映射）。
- **token 计数**：`model.get_num_tokens_from_messages(messages)`（替代 `LiteLLMProvider.count_tokens`，截断/压缩判定）。
- **上下文窗口**：模型默认表（`get_model_context_window(model)`），deepseek 等走已知映射。

## 3. 配置兼容

- `config.create_llm()` → `create_chat_model(self.data["llm"])`。
- `create_tool_calling_llm` → 装配 `create_chat_model(llm_cfg, tools=registry.get_all_tools())`，注入编排。
- 环境变量 `AGENT_MODEL`/`AGENT_BASE_URL`/`AGENT_API_KEY` 语义不变（`_ENV_OVERRIDES` 映射到 `llm` 段 → ChatOpenAI 参数）。

## 4. 验收要点

- deepseek 经 OpenAI 兼容 base_url 的 tool calling 正常（**V-01 验证项**）。
- 一次 LLM 调用后，审计/事件 `tokens_in/out` 与 `response_metadata["token_usage"]` 一致（SC-005）。
- `agent run "..."` 走 ChatOpenAI 完成含工具任务，行为与迁移前等价（SC-002）。
