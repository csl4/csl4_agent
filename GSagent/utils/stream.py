"""agent 主循环的事件协议（拆壳后：langgraph custom 流事件）。

节点经 ``langgraph.config.get_stream_writer()`` 推送自定义事件（``stream_custom``），
CLI/serve 直接消费 ``graph.stream(..., stream_mode="custom")`` 流里的
``{"type": <StreamEvents.value>, "data": {...}}`` 字典。SSE 序列化收敛为
``event_to_sse``。

多 Agent 事件（T017，向后兼容）：MULTI_AGENT_DECOMPOSE / MULTI_AGENT_SUBAGENT /
MULTI_AGENT_DONE。Plan 事件（US3 T028）：PLAN / PLAN_TASK。摘要压缩事件：SUMMARY。
"""

import json
from enum import Enum
from typing import Any, Dict


class StreamEvents(str, Enum):
    """agent 主循环期间发出的渲染事件类型（custom 事件 type 标签）。"""

    ANSWER_DELTA = "ai_answer_delta"
    ANSWER_END = "ai_answer_end"
    START_TOOL = "start_tool_calling"
    TOOL_RESULT = "tool_calling_result"
    ERROR = "error"
    AI_MESSAGE = "ai_message"
    APPROVAL_REQUIRED = "approval_required"
    TOKEN_COUNT = "token_count"
    SUMMARY = "summary"  # 摘要式上下文压缩命中（data: old_count/new_count/current_tokens/max_tokens）
    FRONTEND_PAUSE = "frontend_pause"
    # ---- 多Agent 编排事件（T017，向后兼容新增）----
    MULTI_AGENT_DECOMPOSE = "multi_agent_decompose"
    MULTI_AGENT_SUBAGENT = "multi_agent_subagent"
    MULTI_AGENT_DONE = "multi_agent_done"
    # ---- 企业级升级事件（002-enterprise-cli-upgrade，向后兼容新增）----
    USAGE = "usage"  # 回合用量（US2，data 含 usage/cost，contracts/cli.md）
    # Plan-and-Execute（US3 T028，FR-008，contracts/cli.md）：
    #   PLAN     = DAG 规划完成（data: tasks/batches）
    #   PLAN_TASK = 子任务开始/结果（data: task_id/description/status/result/batch）
    PLAN = "plan"
    PLAN_TASK = "plan_task"


def stream_custom(writer: Any, event: StreamEvents, data: Dict[str, Any]) -> None:
    """节点内经 ``get_stream_writer()`` 推送一条渲染事件。

    事件形态：``{"type": event.value, "data": data}``。
    """
    writer({"type": event.value, "data": data})


def event_to_sse(ev: Dict[str, Any]) -> str:
    """custom 事件 dict → SSE 文本 ``event:xxx\\ndata:{json}\\n\\n``。"""
    return f"event: {ev.get('type', '')}\ndata: {json.dumps(ev.get('data', {}), default=str)}\n\n"


__all__ = ["StreamEvents", "event_to_sse", "stream_custom"]
