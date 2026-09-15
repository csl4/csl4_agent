"""LLM 供应商装配（纯 langgraph 重构）。

002-langchain-ecosystem 后 LLM 装配走 ``create_chat_model()``（langchain ChatOpenAI）；
``LiteLLMProvider`` 执行路径已移除（T029）；旧 ``LLM``/``ModelResponse`` 抽象
（providers/base.py）已随纯 langgraph 重构删除（完全纯）。
"""

from GSagent.core.providers.factory import create_chat_model

__all__ = ["create_chat_model"]
