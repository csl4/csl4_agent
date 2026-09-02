"""LLM 供应商抽象与实现（原 core/llm.py）。

原 `from agent.core.llm import ...` 的站点改为 `from agent.core.providers import ...`。
"""

from agent.core.providers.base import LLM, ModelResponse
from agent.core.providers.litellm_provider import LiteLLMProvider

__all__ = ["LLM", "LiteLLMProvider", "ModelResponse"]
