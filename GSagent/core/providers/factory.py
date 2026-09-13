"""LLM 装配工厂（002-langchain-ecosystem，contracts/llm.md §1）。

用 langchain 原生模型类（ChatOpenAI）替换 LiteLLMProvider。``base_url`` 指向
OpenAI 兼容网关（deepseek 等）。``tools`` 传入时 ``bind_tools`` 启用 tool calling。

``langchain-openai`` 未安装时 raise 明确错误（提示管理员授权安装）——config
装配是运行时依赖，测试用 FakeChatLLM 直接注入不经过本工厂。
"""

from typing import Any, Dict, List, Optional


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

    model = str(cfg.get("model") or "") or None
    api_key = str(cfg.get("api_key") or "") or None
    base_url = str(cfg.get("base_url") or "") or None

    chat = ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
    if tools:
        chat = chat.bind_tools(tools)
    return chat


__all__ = ["create_chat_model"]
