"""LLM 供应商抽象与实现。

002-langchain-ecosystem 后 LLM 装配走 ``GSagent/core/providers/factory.py``
的 ``create_chat_model()``（langchain ChatOpenAI）；``LiteLLMProvider`` 执行路径
已移除（T029）。本包仅保留抽象基类（外部/测试兼容 re-export）。
"""

from GSagent.core.providers.base import LLM, ModelResponse
from GSagent.core.providers.factory import create_chat_model

__all__ = ["LLM", "ModelResponse", "create_chat_model"]
