"""LLM 装配工厂（002-langchain-ecosystem，contracts/llm.md §1）。

用 langchain 原生模型类（ChatOpenAI）替换 LiteLLMProvider。``base_url`` 指向
OpenAI 兼容网关（deepseek 等）。``tools`` 传入时 ``bind_tools`` 启用 tool calling。

``langchain-openai`` 未安装时 raise 明确错误（提示管理员授权安装）——config
装配是运行时依赖，测试用 FakeChatLLM 直接注入不经过本工厂。
"""

from typing import Any, Dict, List, Optional

# litellm 风格 provider 前缀（deepseek/xxx → xxx）。ChatOpenAI 把 model 原样
# 作为 API 的 model 参数；OpenAI 兼容网关（deepseek 等）期望裸模型 id，
# 不认 litellm 的 provider/model 路由名（contracts/llm.md §3 配置语义保留）。
_LITELLM_PROVIDERS = {
    "deepseek",
    "openai",
    "azure",
    "anthropic",
    "google",
    "groq",
    "mistral",
    "together",
    "fireworks",
}


def _normalize_model_name(model: str) -> str:
    """剥离 litellm 风格 provider 前缀（``deepseek/deepseek-v4-flash`` → ``deepseek-v4-flash``）。

    仅当形如 ``known_provider/model_id`` 时剥离；其余（裸 id / 含 / 的自定义名）原样返回。
    """
    text = (model or "").strip()
    if "/" in text:
        provider, _, rest = text.partition("/")
        if provider in _LITELLM_PROVIDERS and rest:
            return rest
    return text


def create_chat_model(
    config: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Any]] = None,
) -> Any:
    """按 llm 配置段装配 ChatOpenAI（可选 bind_tools）。

    参数:
        config: ``llm`` 配置段 dict（model/api_key/base_url）。
        tools: langchain 工具列表；提供时 ``bind_tools``。

    返回:
        ChatOpenAI 实例（BaseChatModel）。
    """
    cfg = config or {}
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - 依赖未装提示
        raise RuntimeError(
            "langchain-openai 未安装。请在管理员 PowerShell 授权后执行："
            "uv pip install 'langchain-openai>=0.3'"
        ) from exc

    model = _normalize_model_name(str(cfg.get("model") or "") or "")
    api_key = str(cfg.get("api_key") or "") or None
    base_url = str(cfg.get("base_url") or "") or None

    try:
        chat = ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
    except Exception as exc:  # noqa: BLE001 - 构造失败收敛为可读配置错误
        raise RuntimeError(
            "LLM 装配失败：未配置有效的 API Key。请设置环境变量 "
            "AGENT_API_KEY（或 OPENAI_API_KEY），或在 .GSagent/config.yaml 的 "
            "llm.api_key 中配置（base_url 指向 OpenAI 兼容网关，如 deepseek）。"
        ) from exc
    if tools:
        chat = chat.bind_tools(tools)
    return chat


__all__ = ["create_chat_model"]
