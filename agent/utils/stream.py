"""供 agent 主循环使用的 SSE（Server-Sent Events）流消息定义。"""


# ======================= 中文导览 =======================
# 本文件定义主循环对外的【事件协议】：
#   StreamEvents  → 事件枚举：ANSWER_DELTA(打字机式吐字) / START_TOOL / TOOL_RESULT /
#                    ANSWER_END(最终答案) / APPROVAL_REQUIRED(审批暂停) / FRONTEND_PAUSE /
#                    COMPACTION_START / COMPACTED / ERROR 等。
#   StreamMessage → 单个事件，带 to_sse() 转成 SSE 文本 `event:xxx\ndata:{json}\n\n`。
# 数据流位置：ToolCallingLLM.call_stream() 逐个 yield StreamMessage；前端据此渲染/cli据此 print。
# =========================================================

import json
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel


class StreamEvents(str, Enum):
    """agent 主循环期间发出的事件类型。"""

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
    """agent 主循环发出的单条 SSE 格式消息。

    示例 SSE 格式：
        event: {event}\n
        data: {json}\n\n
    """

    event: StreamEvents
    data: Dict[str, Any] = {}

    def to_sse(self) -> str:
        """格式化为 SSE 字符串。"""
        return f"event: {self.event.value}\ndata: {json.dumps(self.data, default=str)}\n\n"