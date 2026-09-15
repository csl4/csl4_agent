"""面向图的新外壳（纯 langgraph 重构，Phase 1）。

取代 ``ToolCallingLLM.call_stream`` 的 StreamMessage 契约：直接暴露编译后的
LangGraph 图，CLI/serve 通过 ``GraphAgent.stream()`` 消费 updates 流并处理
``interrupt``（审批/前端暂停，per-interrupt-id 恢复）。

与旧外壳的差异：
- 审批语义下沉到工具包装器（``tools/approval.py``），图用 ``ToolNode``。
- 暂停恢复：``Command(resume={interrupt_id: {"approved": bool}})``。
- checkpointer / store 可注入（SqliteSaver / SqliteStore）。

``stream()`` 产出两类对象（CLI 区分处理）：
- ``StreamMessage``：渲染事件（ANSWER_DELTA/START_TOOL/TOOL_RESULT/ANSWER_END/USAGE…）
- ``PauseRequest``：审批/前端暂停（含 ``id`` 供恢复）
"""

import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.types import Command

from GSagent.core.llm_adapter import dict_to_messages
from GSagent.core.observability import (
    AgentEventType,
    CostEstimator,
    EventEmitter,
    MemoryEventStore,
)
from GSagent.core.orchestration.graph import build_graph_agent
from GSagent.core.orchestration.nodes import _emit
from GSagent.core.policy.audit import AuditLog
from GSagent.core.policy.hitl import HitlPolicy
from GSagent.core.tools.registry import ToolRegistry
from GSagent.core.truncation.compaction import SessionCompactor
from GSagent.core.truncation.input_context_window_limiter import ContextWindowLimiter
from GSagent.utils.stream import StreamEvents, StreamMessage

logger = logging.getLogger(__name__)


@dataclass
class PauseRequest:
    """审批/前端暂停（langgraph interrupt 的透传视图）。

    - ``id``：langgraph Interrupt.id，恢复时作为 ``Command(resume={id: ...})`` 的 key。
    - ``type``：``approval`` | ``frontend``。
    - ``value``：interrupt payload（CLI 据此渲染审批请求）。
    """

    id: str
    type: str
    value: Dict[str, Any] = field(default_factory=dict)


class GraphAgent:
    """面向图的新外壳：编译并暴露 LangGraph 图，直接消费图流。

    构造即编译（``build_graph_agent``）；``stream()`` 是 CLI/serve 的唯一入口，
    产出 ``StreamMessage`` 渲染事件与 ``PauseRequest`` 暂停请求。
    """

    def __init__(
        self,
        chat_model: Optional[BaseChatModel] = None,
        *,
        graph: Optional[Any] = None,
        tools_registry: Optional[ToolRegistry] = None,
        max_steps: int = 20,
        enable_compaction: bool = True,
        compaction_threshold_ratio: float = 0.75,
        compaction_keep_last_n: int = 6,
        agent_id: str = "main",
        hitl_policy: Optional[HitlPolicy] = None,
        audit_log: Optional[AuditLog] = None,
        cost_estimator: Optional[CostEstimator] = None,
        record_usage: bool = True,
        event_emitter: Optional[EventEmitter] = None,
        tracer: Optional[Any] = None,
        input_guard: Optional[Any] = None,
        output_guard: Optional[Any] = None,
        checkpointer: Optional[Any] = None,
        store: Optional[Any] = None,
    ) -> None:
        self.chat_model = chat_model
        self.model_name = (
            getattr(chat_model, "model_name", "") or getattr(chat_model, "model", "") or "chat-model"
        )
        self.agent_id = agent_id
        self.tools_registry = tools_registry or ToolRegistry()
        self.max_steps = max_steps
        self.enable_compaction = enable_compaction
        self.compaction_threshold_ratio = compaction_threshold_ratio
        self.compaction_keep_last_n = compaction_keep_last_n
        self.hitl_policy = hitl_policy
        self.audit_log = audit_log
        self.cost_estimator = cost_estimator
        self.record_usage = record_usage
        self.event_emitter = event_emitter or EventEmitter(MemoryEventStore())
        self._tracer = tracer
        self.input_guard = input_guard
        self.output_guard = output_guard

        self._compactor = (
            SessionCompactor(llm=chat_model, keep_last_n=compaction_keep_last_n)
            if chat_model is not None
            else None
        )
        self._limiter = (
            ContextWindowLimiter(llm=chat_model, threshold_ratio=compaction_threshold_ratio)
            if chat_model is not None
            else None
        )
        self._saver = checkpointer
        self._store = store
        if graph is not None:
            # 外部装配的图（多 Agent / Plan）：初始状态只需 messages 通道
            self._graph = graph
            self._external_graph = True
        else:
            self._graph = build_graph_agent(
                self, checkpointer=checkpointer, store=store
            )
            self._external_graph = False

    # ---- 图状态构造 ----
    def _build_initial_state(
        self,
        *,
        messages: List[Dict[str, Any]],
        request_context: Optional[Dict[str, Any]],
        cancel_event: Any,
        enable_tool_approval: bool,
    ) -> Dict[str, Any]:
        """构造图初始状态。

        外部注入图（多 Agent / Plan）只需 messages + request_context 通道；
        单 Agent 图（自建 build_graph_agent）补充编排内部字段。
        """
        state: Dict[str, Any] = {
            "messages": dict_to_messages(messages),
            "request_context": request_context,
            "cancel_event": cancel_event,
            "_stream_messages": [],
        }
        if not self._external_graph:
            state.update(
                last_tool_calls=[],
                prev_tool_calls=[],
                no_progress_streak=0,
                iteration=0,
                tool_number=0,
                terminated=None,
                enable_tool_approval=bool(enable_tool_approval),
                _events=[],
            )
        return state

    # ---- 对外入口 ----
    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        session_id: str,
        resume: Optional[Dict[str, Any]] = None,
        request_context: Optional[Dict[str, Any]] = None,
        cancel_event: Any = None,
    ) -> Generator[Any, None, None]:
        """运行图，产出 ``StreamMessage`` / ``PauseRequest``。

        参数:
            messages: 本轮初始消息（OpenAI dict，经 dict_to_messages 入图）。
            session_id: thread_id（会话/断点持久化维度）。
            resume: 恢复决策 ``{interrupt_id: {"approved": bool}}``（审批暂停后
                下次调用传入）；首跑为 None。
        """
        config = {"configurable": {"thread_id": str(session_id)}}
        rc = dict(request_context or {})
        rc.setdefault("session_id", session_id)
        if resume:
            stream_input: Any = Command(resume=resume)
        else:
            stream_input = self._build_initial_state(
                messages=messages,
                request_context=rc,
                cancel_event=cancel_event,
                enable_tool_approval=True,
            )
        _emit(
            self,
            AgentEventType.AGENT_START,
            session_id=session_id,
            message=AgentEventType.AGENT_START.value,
        )
        try:
            yield from self._run_graph(stream_input, config, cancel_event)
        finally:
            _emit(
                self,
                AgentEventType.AGENT_END,
                session_id=session_id,
                message=AgentEventType.AGENT_END.value,
            )

    def _run_graph(
        self,
        stream_input: Any,
        config: Dict[str, Any],
        cancel_event: Any,
    ) -> Generator[Any, None, None]:
        """遍历图，yield 渲染事件与暂停请求。"""
        saw_terminal = False
        span_ctx = (
            self._tracer.start_as_current_span("invoke_agent")
            if self._tracer is not None
            else nullcontext()
        )
        try:
            with span_ctx as span:
                if span is not None:
                    span.set_attribute("gen_ai.operation.name", "invoke_agent")
                    span.set_attribute("agent_id", self.agent_id)
                for chunk in self._graph.stream(
                    stream_input, config, stream_mode="updates"
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        saw_terminal = True
                        yield StreamMessage(
                            event=StreamEvents.ERROR, data={"error": "   cancelled by user."}
                        )
                        return
                    if "__interrupt__" in chunk:
                        saw_terminal = True
                        for intr in chunk["__interrupt__"]:
                            value = intr.value or {}
                            ptype = "approval" if value.get("type") == "approval" else "frontend"
                            yield PauseRequest(id=intr.id, type=ptype, value=value)
                        return
                    for _node_name, node_updates in chunk.items():
                        if _node_name == "__interrupt__":
                            continue
                        node_updates = node_updates or {}
                        for sm_data in node_updates.get("_stream_messages") or []:
                            sm = (
                                sm_data
                                if isinstance(sm_data, StreamMessage)
                                else StreamMessage(**sm_data)
                            )
                            if sm.event in (StreamEvents.ANSWER_END, StreamEvents.ERROR):
                                saw_terminal = True
                            yield sm
        except Exception as exc:  # noqa: BLE001 - 编排兜底
            logger.exception("LangGraph graph execution failed: %s", exc)
            yield StreamMessage(
                event=StreamEvents.ERROR, data={"error": f"LangGraph graph execution failed: {exc}"}
            )
            return
        if not saw_terminal:
            yield StreamMessage(
                event=StreamEvents.ERROR,
                data={"error": "LangGraph graph finished without a terminal answer event."},
            )

    # ---- 便捷 ----
    def set_hitl_mode(self, mode: str) -> None:
        """运行时切换 HITL 模式（`/hitl <mode>`）。"""
        if self.hitl_policy is None:
            raise ValueError("No hitl_policy configured on this agent.")
        self.hitl_policy.set_mode(mode)

    @property
    def graph(self) -> Any:
        """编译后的 CompiledStateGraph（高级用法：get_state/invoke）。"""
        return self._graph


__all__ = ["GraphAgent", "PauseRequest"]
