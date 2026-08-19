"""LLM abstraction layer with LiteLLM provider."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from agent.core.models import ContextWindowUsage


class ModelResponse(BaseModel):
    """Typed wrapper for LLM completion responses."""

    content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = []
    model: str = ""
    usage: Optional[ContextWindowUsage] = None

    class Config:
        """Pydantic config for ModelResponse."""

        arbitrary_types_allowed = True


class LLM(ABC):
    """Abstract base for LLM providers.

    Subclasses implement the actual API calls (LiteLLM, boto3, etc.).
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
        """Send a completion request to the LLM.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Tool definitions in OpenAI format.
            tool_choice: "auto", "none", or "required".
            temperature: Sampling temperature.
            stream: Whether to stream the response.
            response_format: Optional response format spec.
            drop_params: Whether to drop unsupported params.

        Returns:
            ModelResponse with content and/or tool_calls.
        """
        ...

    @abstractmethod
    def count_tokens(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ContextWindowUsage:
        """Count tokens for the given messages and tools."""
        ...

    @abstractmethod
    def get_context_window_size(self) -> int:
        """Return the maximum context window size for this model."""
        ...

    @abstractmethod
    def get_maximum_output_token(self) -> int:
        """Return the maximum output tokens for this model."""
        ...


class LiteLLMProvider(LLM):
    """LLM provider backed by LiteLLM."""

    def __init__(self, model: str, api_key: str = "", base_url: str = "", **kwargs: Any):
        super().__init__(model=model, api_key=api_key, base_url=base_url, **kwargs)

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
        """Send a completion request via LiteLLM."""
        import litellm

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
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

        response = litellm.completion(**kwargs)

        choice = response.choices[0]
        message = choice.message

        return ModelResponse(
            content=getattr(message, "content", None),
            tool_calls=getattr(message, "tool_calls", None) or [],
            model=response.model or self.model,
            usage=ContextWindowUsage(
                total_tokens=getattr(response.usage, "total_tokens", 0),
                prompt_tokens=getattr(response.usage, "prompt_tokens", 0),
                completion_tokens=getattr(response.usage, "completion_tokens", 0),
            ),
        )

    def count_tokens(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ContextWindowUsage:
        """Count tokens using litellm.token_counter."""
        import litellm

        try:
            total = litellm.token_counter(model=self.model, messages=messages, tools=tools)
            return ContextWindowUsage(total_tokens=total)
        except Exception:
            # Fallback: rough estimate
            import json

            text = json.dumps(messages, default=str)
            return ContextWindowUsage(total_tokens=len(text) // 4)

    def get_context_window_size(self) -> int:
        """Return the context window size for the model."""
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
        }
        for key, size in context_windows.items():
            if key in self.model.lower():
                return size
        return 128000  # Default

    def get_maximum_output_token(self) -> int:
        """Return the maximum output tokens for the model."""
        return 4096  # Common default