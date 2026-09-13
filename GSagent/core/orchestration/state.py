"""编排图状态 schema（LangGraph GraphState）。

彻底 langgraph 化（用户要求）：删掉手写的暂停状态字段
（``pause``/``tool_decisions``/``frontend_tool_results``/``pending_approvals``），
审批/前端暂停改由 langgraph ``interrupt()`` 承载（人在回环），恢复用
``Command(resume=...)`` + checkpointer（thread_id 固定，真正断点续跑）。

保留字段：
- ``messages``：OpenAI 格式 dict 列表，**覆盖语义**（节点返回完整列表；
  LLM 抽象是 dict-based，不改 langchain 消息层）。
- ``terminated``：熔断/取消/输入拦截（max_steps/cancelled/blocked）——这些是
  图提前结束信号，非暂停，不走 interrupt。
- ``prev_tool_calls`` / ``no_progress_streak``：死循环检测（宪法 10.1）。
- 回合配置（``enable_tool_approval``/``request_context``/``cancel_event``）：
  call_stream 每次经初始状态传入，使图编译一次、每回合复用。
"""

from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _append_list(left: Optional[List[Any]], right: List[Any]) -> List[Any]:
    """Append reducer：节点返回的缓冲片段追加到累积值。"""
    return (left or []) + (right or [])


class GraphState(TypedDict, total=False):
    """Agent 主循环的编排图状态。"""

    # 对话消息：langchain BaseMessage 列表，add_messages 追加式合并
    # （Human/AI/Tool 自动按 tool_call_id 配对；002-langchain-ecosystem）
    messages: Annotated[list[BaseMessage], add_messages]

    # 工具调用（tools 节点消费）
    last_tool_calls: List[Dict[str, Any]]
    prev_tool_calls: List[Dict[str, Any]]  # 上一轮（死循环比较）
    no_progress_streak: int  # 连续相同调用计数（≥3 死循环终止）

    # 编排元信息
    iteration: int  # 当前步数（LLM 调用计数）
    tool_number: int  # 工具编号偏移
    terminated: Optional[str]  # None | "max_steps" | "cancelled" | "blocked"

    # 回合级配置（call_stream 每次经初始状态传入）
    enable_tool_approval: bool
    request_context: Optional[Dict[str, Any]]
    cancel_event: Optional[Any]  # threading.Event；单机 in-memory 可持有对象引用

    # 事件缓冲（append reducer；前缀 _：编排内部，不进 LLM 上下文）
    _events: Annotated[List[Dict[str, Any]], _append_list]
    _stream_messages: Annotated[List[Dict[str, Any]], _append_list]


__all__ = ["GraphState", "_append_list"]
