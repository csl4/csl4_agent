"""供 agent 主循环使用的 SSE（Server-Sent Events）流消息定义。"""


# ======================= 中文导览 =======================
# 本文件定义主循环对外的【事件协议】：
#   StreamEvents  → 事件枚举：ANSWER_DELTA(打字机式吐字) / START_TOOL / TOOL_RESULT /
#                    ANSWER_END(最终答案) / APPROVAL_REQUIRED(审批暂停) / FRONTEND_PAUSE /
#                    COMPACTION_START / COMPACTED / ERROR 等。
#   StreamMessage → 单个事件，带 to_sse() 转成 SSE 文本 `event:xxx\ndata:{json}\n\n`。
# 多Agent 事件（T017，向后兼容）：MULTI_AGENT_DECOMPOSE(任务拆解) /
#   MULTI_AGENT_SUBAGENT(SubAgent 启停/结果) / MULTI_AGENT_DONE(任务完成)。
#   既有事件消费方无需改动 —— 新事件只是新增枚举成员（宪法 II）。
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
    COMPACTION_START = "compaction_start"
    COMPACTED = "compacted"
    FRONTEND_PAUSE = "frontend_pause"
    # ---- 多Agent 编排事件（T017，向后兼容新增）----
    MULTI_AGENT_DECOMPOSE = "multi_agent_decompose"
    MULTI_AGENT_SUBAGENT = "multi_agent_subagent"
    MULTI_AGENT_DONE = "multi_agent_done"


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
