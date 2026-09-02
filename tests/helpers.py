"""共享测试桩（离线确定性，供各集成/单测复用）。

LLM 说明：以脚本化 ScriptedLLM 在 LLM 抽象层打桩（本机离线；且 litellm 走
httpx 传输、`responses` 无法拦截其请求 —— 因此不适用 responses 的 HTTP
mock，而用依赖注入桩，符合宪法「HTTP mock 用 responses，不用
@patch('requests.get')」的离线原则）。
"""

from agent.core.models import ContextWindowUsage
from agent.core.providers import LLM, ModelResponse


class ScriptedLLM(LLM):
    """按调用顺序返回预置回复的假 LLM（离线确定性测试）。

    记录每次调用的 messages（验证上下文/提示词注入）；回复耗尽后返回
    "(fallback)"。窗口/输出上限给固定大值，保证测试链路不触截断。
    """

    def __init__(self, responses: list) -> None:
        super().__init__(model="fake-model")
        self.responses = list(responses)
        self.calls: list = []

    def completion(self, messages, tools=None, tool_choice="auto", temperature=0.7,
                   stream=False, response_format=None, drop_params=True) -> ModelResponse:
        self.calls.append(messages)
        if self.responses:
            return ModelResponse(content=self.responses.pop(0))
        return ModelResponse(content="(fallback)")

    def count_tokens(self, messages, tools=None) -> ContextWindowUsage:
        return ContextWindowUsage(total_tokens=1)

    def get_context_window_size(self) -> int:
        return 128000

    def get_maximum_output_token(self) -> int:
        return 4096
