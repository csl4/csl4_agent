"""LangGraph 编排节点实现（002-langchain-ecosystem：BaseMessage 化）。

编排内部全部流转 langchain 对象：
- 消息：``BaseMessage`` 列表（``add_messages`` reducer 追加合并）
- LLM：``loop.chat_model``（BaseChatModel，bind_tools），``invoke`` 得 ``AIMessage``
- 工具：langchain ``BaseTool`` 注册表 + 守卫/审批包装层（``tools/registry.py``）
- 暂停（审批/前端）：``interrupt()``（人在回环），恢复 ``Command(resume=...)``

外部契约：``call_stream`` 输入 OpenAI dict 经 ``dict_to_messages`` 转换；事件中的
消息快照经 ``messages_to_dict`` 输出（消费方零改动）。
"""

from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
)

from GSagent.core.llm_adapter import (
    dict_to_messages,
    extract_usage,
    messages_to_dict,
)
from GSagent.core.observability import (
    AgentEventEnvelope,
    AgentEventType,
    LogLevel,
)
from GSagent.core.observability.models import payload_redacted
from GSagent.utils.stream import StreamEvents, StreamMessage


# ---- 事件缓冲辅助 ----
def _msg(event: StreamEvents, data: Dict[str, Any]) -> Dict[str, Any]:
    """构造缓冲用 StreamMessage 的 dict 形态。"""
    return {"event": event.value, "data": data}


def _current_trace_ids() -> tuple:
    """从 OTel 当前 span context 提取 (trace_id, span_id, parent_span_id)。"""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return "", "", None
        return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}", None
    except Exception:  # noqa: BLE001 - 可观测失败不阻塞
        return "", "", None


def _ctx_ids(state: Dict[str, Any]) -> tuple:
    """从 request_context 提取 (session_id, task_id)。"""
    rc = state.get("request_context") or {}
    return (
        str(rc.get("session_id", "") or ""),
        str(rc.get("task_id", "") or ""),
    )


def _emit(
    loop: Any,
    event_type: AgentEventType,
    *,
    state: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    task_id: str = "",
    message: str = "",
    payload: Optional[Dict[str, Any]] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    level: LogLevel = LogLevel.INFO,
) -> None:
    """构造 AgentEventEnvelope 并发射（event_emitter 为 None 时零开销）。"""
    if loop.event_emitter is None:
        return
    if state is not None and not (session_id or task_id):
        session_id, task_id = _ctx_ids(state)
    trace_id, span_id, _ = _current_trace_ids()
    env = AgentEventEnvelope(
        trace_id=trace_id,
        span_id=span_id,
        session_id=session_id,
        task_id=task_id,
        agent_id=loop.agent_id,
        event_type=event_type,
        level=level,
        message=message,
        payload=payload_redacted(payload or {}),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )
    loop.event_emitter.emit(env)


def _same_tool_calls(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    """比较两轮工具调用是否相同（name + args，宪法 10.1 死循环判定）。"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x.get("name") != y.get("name"):
            return False
        if x.get("args") != y.get("args"):
            return False
    return True


# ---- 节点：输入侧 Guardrail ----
def guard_in_node(loop: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """输入侧 Guardrail：校验最新用户输入；拦截则终止本轮。"""

    def _guard_in(state: Dict[str, Any]) -> Dict[str, Any]:
        guard = getattr(loop, "input_guard", None)
        if guard is None:
            return {"_stream_messages": []}
        messages = state.get("messages") or []
        last_user = next(
            (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
        )
        text = last_user.content if last_user else ""
        if isinstance(text, str):
            result = guard.check(text)
            if not result.allowed:
                session_id, _ = _ctx_ids(state)
                if loop.audit_log is not None:
                    loop.audit_log.record(
                        event_type="guardrail_input",
                        payload={
                            "matched_rule": result.matched_rule,
                            "reason": result.reason,
                        },
                        outcome="blocked",
                        session_id=session_id,
                    )
                return {
                    "_stream_messages": [
                        _msg(
                            StreamEvents.ERROR,
                            {
                                "error": f"Input blocked: {result.reason}",
                                "guardrail": True,
                            },
                        )
                    ],
                    "terminated": "blocked",
                }
        return {"_stream_messages": []}

    return _guard_in


# ---- 节点：Agent（LLM 调用核心）----
def agent_node(loop: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Agent 节点：压缩 → 调 LLM（bind_tools）→ 判定（熔断/死循环）。"""

    def _agent(state: Dict[str, Any]) -> Dict[str, Any]:
        messages: List[BaseMessage] = list(state.get("messages") or [])
        iteration: int = int(state.get("iteration", 0)) + 1
        stream_msgs: List[Dict[str, Any]] = []

        cancel_event = state.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            stream_msgs.append(_msg(StreamEvents.ERROR, {"error": "   cancelled by user."}))
            return {"_stream_messages": stream_msgs, "terminated": "cancelled"}

        # 压缩（仅影响本次 LLM 输入；TODO(002)：长会话持久化）
        if loop.enable_compaction and iteration < loop.max_steps:
            dict_msgs = messages_to_dict(messages)
            if loop._limiter.check_compaction_needed(dict_msgs, []):
                stream_msgs.append(
                    _msg(
                        StreamEvents.COMPACTION_START,
                        {"message_count": len(messages)},
                    )
                )
                messages = dict_to_messages(loop._compactor.compact(dict_msgs))
                stream_msgs.append(
                    _msg(StreamEvents.COMPACTED, {"new_message_count": len(messages)})
                )

        # LLM 调用（bind_tools 原生 tool calling）
        _emit(
            loop,
            AgentEventType.LLM_REQUEST,
            state=state,
            message="llm request",
            payload={"iteration": iteration},
        )
        response: AIMessage = loop.chat_model.invoke(messages)

        # 流式 delta（非流式 invoke：content 单块）
        if response.content:
            stream_msgs.append(_msg(StreamEvents.ANSWER_DELTA, {"content": response.content}))

        # 审计 + 事件（usage 经 extract_usage）
        usage = extract_usage(response)
        cost_usd = 0.0
        if loop.record_usage and loop.cost_estimator is not None and usage.total_tokens:
            cost_usd = loop.cost_estimator.estimate(
                str(getattr(response, "response_metadata", {}).get("model", "")) or loop.model_name,
                usage.prompt_tokens,
                usage.completion_tokens,
            )
        _emit(
            loop,
            AgentEventType.LLM_RESPONSE,
            state=state,
            message="llm call",
            payload={"model": loop.model_name},
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cost_usd=cost_usd,
        )
        # 企业级审计：每次 LLM 调用一条 model_call（含 token/成本，FR-005/007）
        if loop.audit_log is not None:
            session_id, task_id = _ctx_ids(state)
            loop.audit_log.record(
                event_type="model_call",
                payload={"model": loop.model_name},
                session_id=session_id,
                usage={
                    "total_tokens": usage.total_tokens,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "model": loop.model_name,
                    "estimated_cost": round(cost_usd, 6),
                },
            )

        # 判定
        last_tool_calls: List[Dict[str, Any]] = []
        terminated: Optional[str] = None
        no_progress_streak = int(state.get("no_progress_streak", 0))
        if not response.tool_calls:
            final_content = response.content or ""
            guard = getattr(loop, "output_guard", None)
            if guard is not None:
                final_content = guard.check(str(final_content)).final_content
            all_msgs = messages + [response]
            stream_msgs.append(
                _msg(
                    StreamEvents.USAGE,
                    {
                        "usage": {
                            "total_tokens": usage.total_tokens,
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "model": loop.model_name,
                            "estimated_cost": round(cost_usd, 6),
                        }
                    },
                )
            )
            stream_msgs.append(
                _msg(
                    StreamEvents.ANSWER_END,
                    {
                        "content": final_content,
                        "messages": messages_to_dict(all_msgs),
                        "num_llm_calls": iteration,
                        "usage": {
                            "total_tokens": usage.total_tokens,
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "model": loop.model_name,
                            "estimated_cost": round(cost_usd, 6),
                        },
                    },
                )
            )
        else:
            last_tool_calls = [dict(tc) for tc in response.tool_calls]
            if _same_tool_calls(
                [dict(tc) for tc in response.tool_calls], state.get("prev_tool_calls") or []
            ):
                no_progress_streak += 1
            else:
                no_progress_streak = 0
            if no_progress_streak >= 3:
                terminated = "max_steps"
            elif iteration >= loop.max_steps:
                terminated = "max_steps"

        return {
            "messages": [response],
            "iteration": iteration,
            "last_tool_calls": last_tool_calls,
            "prev_tool_calls": last_tool_calls,
            "no_progress_streak": no_progress_streak,
            "terminated": terminated,
            "_stream_messages": stream_msgs,
        }

    return _agent


# ---- 条件边：判定下一步走向 ----
def make_should_continue(loop: Any) -> Callable[[Dict[str, Any]], str]:
    """条件边：tools（有工具调用且未熔断）或 end。"""

    def _should_continue(state: Dict[str, Any]) -> str:
        if state.get("terminated"):
            return "end"
        if not (state.get("last_tool_calls") or []):
            return "end"
        return "tools"

    return _should_continue


# ---- 节点：输出侧 Guardrail + 熔断终态 ----
def guard_out_node(loop: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """输出侧 Guardrail：处理熔断终态 + 输出校验。"""

    def _guard_out(state: Dict[str, Any]) -> Dict[str, Any]:
        stream_msgs: List[Dict[str, Any]] = []
        messages: List[BaseMessage] = list(state.get("messages") or [])
        terminated: Optional[str] = state.get("terminated")

        if terminated == "max_steps":
            last_ai = next(
                (m for m in reversed(messages) if isinstance(m, AIMessage)), None
            )
            fallback_content = last_ai.content if last_ai else ""
            guard = getattr(loop, "output_guard", None)
            if guard is not None and fallback_content:
                fallback_content = guard.check(str(fallback_content)).final_content
            if fallback_content:
                stream_msgs.append(
                    _msg(
                        StreamEvents.ANSWER_END,
                        {
                            "content": fallback_content,
                            "messages": messages_to_dict(messages),
                            "num_llm_calls": state.get("iteration", 0),
                            "usage": {},
                            "max_steps_reached": True,
                        },
                    )
                )
            else:
                stream_msgs.append(
                    _msg(
                        StreamEvents.ERROR,
                        {
                            "error": (
                                f"Max steps ({loop.max_steps}) reached without a final "
                                "answer. Increase the step limit or simplify the task."
                            ),
                            "messages": messages_to_dict(messages),
                        },
                    )
                )

        return {"_stream_messages": stream_msgs}

    return _guard_out


__all__ = [
    "agent_node",
    "guard_in_node",
    "guard_out_node",
    "make_should_continue",
]
