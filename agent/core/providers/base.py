"""LLM 供应商抽象基类（LLM ABC + ModelResponse 值对象）。"""


# ======================= 中文导览 =======================
# 本文件是「LLM 抽象层」（行为对象）：
#   LLM(ABC)        → 供应商抽象基类。输入 messages(+tools)，输出 ModelResponse；
#                      子类实现真实 API 调用（completion / completion_stream / count_tokens
#                      / get_context_window_size...）。
#   ModelResponse   → 值对象：LLM 返回快照（content + tool_calls + usage + model）。
# 设计理念：上层主循环只认识 LLM 抽象，不绑死某一供应商 —— 换模型/API 不碰主循环。
# 具体供应商实现（如 LiteLLMProvider）放同包的 provider 模块，随时可增。
# =========================================================


from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional

from pydantic import BaseModel

from agent.core.models import ContextWindowUsage


# ---- 值对象：LLM 返回快照 ----
class ModelResponse(BaseModel):
    """LLM 补全（completion）响应的类型化包装。"""

    content: Optional[str] = None #
    tool_calls: List[Dict[str, Any]] = [] #
    model: str = "" #
    usage: Optional[ContextWindowUsage] = None

    class Config:
        """ModelResponse 的 Pydantic 配置。"""

        arbitrary_types_allowed = True


# ---- 行为对象：LLM 供应商抽象基类 ----
# 输入：messages(+tools) 等 OpenAPI 风格参数；输出：ModelResponse。
# 子类实现四件事：completion、completion_stream、count_tokens、get_context_window_size。
# 默认 completion_stream 退化为一次性 completion（无流式能力的 provider 也能跑）。
class LLM(ABC):
    """LLM 供应商的抽象基类。

    子类实现实际的 API 调用（LiteLLM、boto3 等）。
    """

    def __init__(self, model: str, api_key: str = "", base_url: str = "", **kwargs: Any):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra_kwargs = kwargs

    @abstractmethod
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
        """向 LLM 发送一次补全（completion）请求。

        参数:
            messages: OpenAI 格式的聊天消息。
            tools: OpenAI 格式的工具定义。
            tool_choice: "auto"、"none" 或 "required"。
            temperature: 采样温度。
            stream: 是否流式输出响应。
            response_format: 可选的响应格式规范。
            drop_params: 是否丢弃不支持的参数。

        返回:
            包含 content 和/或 tool_calls 的 ModelResponse。
        """
        ...

    def completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.7,
        response_format: Optional[Dict[str, Any]] = None,
        drop_params: bool = True,
    ) -> Generator[str, None, ModelResponse]:
        """流式输出一次补全；产出内容增量，返回完整响应。

        默认回退：执行一次非流式调用，并把全部内容作为单个增量产出，
        因此不支持流式的供应商也能正常工作。
        """
        response = self.completion(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            response_format=response_format,
            drop_params=drop_params,
        )
        if response.content:
            yield response.content
        return response

    @abstractmethod
    def count_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ContextWindowUsage:
        """统计给定消息和工具的 token 数量。"""
        ...

    @abstractmethod
    def get_context_window_size(self) -> int:
        """返回该模型的最大上下文窗口大小。"""
        ...

    @abstractmethod
    def get_maximum_output_token(self) -> int:
        """返回该模型的最大输出 token 数。"""
        ...


__all__ = ["LLM", "ModelResponse"]
