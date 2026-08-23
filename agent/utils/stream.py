"""SSE (Server-Sent Events) stream message definitions for the agent loop."""

from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel


class StreamEvents(str, Enum):
    """Event types emitted during the agent loop."""

    ANSWER_DELTA = "ai_answer_delta"
    ANSWER_END = "ai_answer_end"
    START_TOOL = "start_tool_calling"
    TOOL_RESULT = "tool_calling_result"
    ERROR = "error"
    AI_MESSAGE = "ai_message"
    APPROVAL_REQUIRED = "approval_required"
    TOKEN_COUNT = "token_count"
    COMPACTION_START = "conversation_history_compaction_start"
    COMPACTED = "conversation_history_compacted"
    FRONTEND_PAUSE = "frontend_pause"


class StreamMessage(BaseModel):
    """A single SSE-formatted message emitted by the agent loop.

    Example SSE format:
        event: {event}\n
        data: {json}\n\n
    """

    event: StreamEvents
    data: Dict[str, Any] = {}

    def to_sse(self) -> str:
        """Format as SSE string."""
        import json

        return f"event: {self.event.value}\ndata: {json.dumps(self.data, default=str)}\n\n"