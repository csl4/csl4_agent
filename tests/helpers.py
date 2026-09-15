"""测试共享辅助（离线打桩，不走 HTTP）。

宪法 IV：离线测试用 ``FakeChatLLM``（langchain ``BaseChatModel``）打桩，不走真实
API（无需 responses）。旧 ``ScriptedLLM``（自研 LLM 接口）已随
002-langchain-ecosystem 迁移移除（T029）。
"""

from typing import Any, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr


class FakeChatLLM(BaseChatModel):
    """langchain 离线打桩模型（R-06，002-langchain-ecosystem）。

    按预设 ``AIMessage`` 序列依次响应，用于 langchain 化编排测试（走
    ``_generate``，不走 HTTP）。``AIMessage`` 可带 ``tool_calls`` 与
    ``response_metadata["token_usage"]`` 以验证工具调用与用量提取。

    ``bind_tools`` 返回自身并记录绑定工具（离线打桩：不做真实 schema 绑定，
    也不复制实例——保证 ``calls`` 计数仍落在同一对象上，供测试断言）。
    """

    responses: List[AIMessage] = Field(default_factory=list)
    calls: int = 0  # 已消费的响应数（供测试断言）
    _bound_tools: List[Any] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "FakeChatLLM":
        """离线打桩：记录工具并返回自身（create_agent 内部调用 bind_tools）。"""
        self._bound_tools = list(tools)
        return self

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


__all__ = ["FakeChatLLM"]
