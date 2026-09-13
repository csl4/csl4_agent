"""LLM 供应商抽象与实现（原 core/llm.py）。

原 `from GSagent.core.llm import ...` 的站点改为 `from GSagent.core.providers import ...`。
"""

from GSagent.core.providers.base import LLM, ModelResponse
from GSagent.core.providers.litellm_provider import LiteLLMProvider

__all__ = ["LLM", "LiteLLMProvider", "ModelResponse"]
