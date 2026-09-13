"""测试共享辅助（离线打桩，不走 HTTP）。

宪法 IV：离线测试用 ScriptedLLM 打桩，不走真实 API（无需 responses）。
``ScriptedLLM`` 按预设 ``ModelResponse`` 序列依次响应，供编排图 / 主循环测试
验证调度逻辑（tests/unit/orchestration/test_call_stream_contract.py 等）。
"""

from typing import Any, Dict, Generator, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from GSagent.core.models import ContextWindowUsage
from GSagent.core.providers import LLM, ModelResponse


class ScriptedLLM(LLM):
    """按脚本化响应序列离线打桩的 LLM（不走 HTTP）。

    用法::

        llm = ScriptedLLM([ModelResponse(content="hi", usage=...),
                           ModelResponse(tool_calls=[...])])
    """

    def __init__(self, responses: List[ModelResponse]) -> None:
        super().__init__(model="scripted")
        self._responses = list(responses)
        self.calls = 0  # 已消费的响应数（供测试断言调用次数）

    def completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.7,
        stream: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
        drop_params: bool = True,
    ) -> ModelResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    def completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.7,
        response_format: Optional[Dict[str, Any]] = None,
        drop_params: bool = True,
    ) -> Generator[str, None, ModelResponse]:
        response = self.completion(
            messages, tools, tool_choice, temperature, response_format, drop_params
        )
        if response.content:
            yield response.content
        return response

    def count_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ContextWindowUsage:
        return ContextWindowUsage(total_tokens=1)

    def get_context_window_size(self) -> int:
        return 128000

    def get_maximum_output_token(self) -> int:
        return 4096


class FakeChatLLM(BaseChatModel):
    """langchain 离线打桩模型（R-06，002-langchain-ecosystem）。

    按预设 ``AIMessage`` 序列依次响应，用于 langchain 化编排测试（走
    ``_generate``，不走 HTTP）。``AIMessage`` 可带 ``tool_calls`` 与
    ``response_metadata["token_usage"]`` 以验证工具调用与用量提取。
    """

    responses: List[AIMessage] = Field(default_factory=list)
    calls: int = 0  # 已消费的响应数（供测试断言）

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[idx])])


__all__ = ["ScriptedLLM", "FakeChatLLM"]
