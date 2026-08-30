"""基于 LiteLLM 的 LLM 抽象层。"""


# ======================= 中文导览 =======================
# 本文件是「LLM 抽象层」（行为对象）：
#   LLM(ABC)        → 供应商抽象基类。输入 messages(+tools)，输出 ModelResponse；
#                      子类实现真实 API 调用（completion / completion_stream / count_tokens
#                      / get_context_window_size...）。
#   LiteLLMProvider → 基于 LiteLLM 的具体实现，负责把【流式分片 tool_calls】正确重组完整。
#   ModelResponse   → 值对象：LLM 返回快照（content + tool_calls + usage + model）。
# 设计理念：上层主循环只认识 LLM 抽象，不绑死某一供应商 —— 换模型/API 不碰主循环。
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
    def count_tokens(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ContextWindowUsage:
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


# ---- 行为对象：LiteLLM 具体实现 ----
# 亮点：completion_stream 里 tool_calls 以【分片】到达（首片带 id/name，后续只累加参数片段），
#       这里按 index 分片累加重组，且对 name 的三种发送方式(一次/split/重复)兼容。
class LiteLLMProvider(LLM):
    """由 LiteLLM 支撑的 LLM 供应商实现。"""

    def __init__(self, model: str, api_key: str = "", base_url: str = "", **kwargs: Any):
        super().__init__(model=model, api_key=api_key, base_url=base_url, **kwargs)

    def _completion_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: str,
        temperature: float,
        response_format: Optional[Dict[str, Any]],
        drop_params: bool,
    ) -> Dict[str, Any]:
        """构建共用的 litellm.completion kwargs。"""
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format
        if drop_params:
            kwargs["drop_params"] = drop_params
        return kwargs

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
        """通过 LiteLLM 发送一次补全（completion）请求。"""
        import litellm

        kwargs = self._completion_kwargs(
            messages, tools, tool_choice, temperature, response_format, drop_params
        )
        kwargs["stream"] = stream

        response = litellm.completion(**kwargs)

        choice = response.choices[0]
        message = choice.message

        # LiteLLM returns ChatCompletionMessageToolCall objects; serialize them
        # to OpenAI-format dicts expected by ModelResponse and downstream consumers.
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        tool_calls: List[Dict[str, Any]] = [
            {
                "id": getattr(tc, "id", None) or "",
                "type": getattr(tc, "type", None) or "function",
                "function": {
                    "name": getattr(getattr(tc, "function", None), "name", None) or "",
                    "arguments": getattr(getattr(tc, "function", None), "arguments", None) or "",
                },
            }
            for tc in raw_tool_calls
        ]

        return ModelResponse(
            content=getattr(message, "content", None),
            tool_calls=tool_calls,
            model=response.model or self.model,
            usage=ContextWindowUsage(
                total_tokens=getattr(response.usage, "total_tokens", 0),
                prompt_tokens=getattr(response.usage, "prompt_tokens", 0),
                completion_tokens=getattr(response.usage, "completion_tokens", 0),
            ),
        )

    def completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.7,
        response_format: Optional[Dict[str, Any]] = None,
        drop_params: bool = True,
    ) -> Generator[str, None, ModelResponse]:
        """通过 LiteLLM 流式输出一次补全（completion）。

        逐块产出内容增量字符串，最后返回完整组装好的 ModelResponse
        （content + 重组后的 tool_calls + usage）。
        工具调用（tool_calls）分片按 index 抵达（首个分片携带 id 和 name，
        后续分片只带参数增量）；在此累加，使下游逻辑看到完整响应。
        """
        import litellm

        kwargs = self._completion_kwargs( # 拼接kwargs
            messages, tools, tool_choice, temperature, response_format, drop_params
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        response_stream = litellm.completion(**kwargs)

        content_parts: List[str] = [] # 文本碎片
        # index -> {"id": ..., "name": ..., "arguments": accumulated}
        fragments: Dict[int, Dict[str, str]] = {}# 工具碎片调用重组

        usage: Optional[Any] = None # token 用量暂存 最后一个才提取
        model = self.model

        for chunk in response_stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            model = getattr(chunk, "model", None) or model

            piece = getattr(delta, "content", None)
            if piece:
                content_parts.append(piece)
                yield piece

            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", None)
                idx = 0 if idx is None else idx
                frag = fragments.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    frag["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    name = getattr(fn, "name", None)
                    if name:
                        # Providers either send the name once, split it across
                        # chunks, or repeat it whole on every argument chunk.
                        # Match fragments by prefix so all three reassemble
                        # correctly: a fragment that extends what we already hold
                        # (a growing prefix or the repeated whole name) replaces
                        # it; a disjoint fragment (a split) is appended.
                        if not frag["name"]:
                            frag["name"] = name
                        elif name.startswith(frag["name"]):
                            frag["name"] = name
                        else:
                            frag["name"] += name
                    arguments = getattr(fn, "arguments", None)
                    if arguments:
                        frag["arguments"] += arguments

        tool_calls: List[Dict[str, Any]] = [
            {
                "id": frag["id"],
                "type": "function",
                "function": {"name": frag["name"], "arguments": frag["arguments"]},
            }
            for _, frag in sorted(fragments.items())
        ]

        return ModelResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            model=model,
            usage=ContextWindowUsage(
                total_tokens=getattr(usage, "total_tokens", 0),
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            ),
        )

    def count_tokens(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ContextWindowUsage:
        """使用 litellm.token_counter 统计 token 数量。

        这是粗略估算回退（约每 token 4 个字符）的唯一位置，
        调用方不得重复实现。
        """
        import litellm

        try:
            total = litellm.token_counter(model=self.model, messages=messages, tools=tools)
            return ContextWindowUsage(total_tokens=total)
        except Exception:
            # Fallback: rough estimate
            import json

            text = json.dumps(messages, default=str)
            if tools:
                text += json.dumps(tools, default=str)
            return ContextWindowUsage(total_tokens=len(text) // 4)

    def get_context_window_size(self) -> int:
        """返回该模型的上下文窗口大小。"""
        # Common defaults
        context_windows: Dict[str, int] = {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-4-turbo": 128000,
            "gpt-4": 8192,
            "gpt-3.5-turbo": 16385,
            "claude-3-opus": 200000,
            "claude-3-sonnet": 200000,
            "claude-3-haiku": 200000,
            "claude-3.5-sonnet": 200000,
            "deepseek-v4-flash": 200000,
        }
        for key, size in context_windows.items():
            if key in self.model.lower():
                return size
        return 128000  # Default

    def get_maximum_output_token(self) -> int:
        """返回该模型的最大输出 token 数。"""
        return 4096  # Common default