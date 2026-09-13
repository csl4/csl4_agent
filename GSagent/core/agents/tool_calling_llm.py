"""Agent 核心主循环 —— LangGraph 显式编排的极薄外壳（002-langchain-ecosystem）。

完全 langchain 化：编排内部流转 langchain ``BaseMessage``，LLM 用原生
``BaseChatModel``（ChatOpenAI），工具用 langchain ``BaseTool`` 注册表。
本文件只做**外部契约适配**：
- ``call_stream()``：输入 OpenAI dict 经 ``dict_to_messages`` 转换进图；遍历
  ``self._graph.stream()`` 把节点缓冲的 ``StreamMessage`` 逐个 yield；审批/前端
  暂停由节点 ``interrupt()`` 触发，本层检测 ``__interrupt__`` 产暂停事件后 return。
- **暂停恢复**：下次以 ``tool_decisions``/``frontend_tool_results`` 为参再次调用，
  用 ``Command(resume=...)`` + checkpointer（thread_id 固定）断点续跑。
- 事件中消息快照经 ``messages_to_dict`` 输出（外部消费方零改动）。
"""

import logging
from typing import Any, Dict, Generator, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.types import Command

from GSagent.core.a2a.protocol import Task, TaskState, set_task_state
from GSagent.core.agents.audit_mixin import AuditUsageMixin
from GSagent.core.agents.base_agent import AgentRole, BaseAgent, task_input_text
from GSagent.core.llm_adapter import dict_to_messages
from GSagent.core.observability import (
    AgentEventType,
    CostEstimator,
    EventEmitter,
    MemoryEventStore,
)
from GSagent.core.orchestration.graph import build_graph
from GSagent.core.orchestration.nodes import _emit
from GSagent.core.policy.audit import AuditLog
from GSagent.core.policy.hitl import HitlPolicy
from GSagent.core.prompts import build_chat_messages
from GSagent.core.tools.registry import ToolRegistry
from GSagent.core.truncation.compaction import SessionCompactor
from GSagent.core.truncation.input_context_window_limiter import ContextWindowLimiter
from GSagent.utils.stream import StreamEvents, StreamMessage

logger = logging.getLogger(__name__)


class _CompactionBridge:
    """把 BaseChatModel 适配成压缩模块（limiter/compactor）期望的 dict 接口。

    压缩模块按 OpenAI dict 消息工作；本桥接在内部用 ``dict_to_messages`` 转成
    ``BaseMessage`` 后调 ``chat_model``（get_num_tokens_from_messages / invoke）。
    """

    def __init__(self, chat_model: BaseChatModel) -> None:
        self.chat_model = chat_model

    def count_tokens(self, messages: List[Dict[str, Any]], tools: Optional[list] = None) -> Any:
        bmsgs = dict_to_messages(messages)
        n = self.chat_model.get_num_tokens_from_messages(bmsgs)
        return type("_Usage", (), {"total_tokens": n})()

    def get_context_window_size(self) -> int:
        return int(getattr(self.chat_model, "max_tokens", None) or 0) or 128000

    def completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[list] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Any:
        ai = self.chat_model.invoke(dict_to_messages(messages))
        return type("_Resp", (), {"content": ai.content or ""})()


class ToolCallingLLM(BaseAgent, AuditUsageMixin):
    """LangGraph 编排外壳（AgentRole.MAIN），外部契约不变，内部 BaseMessage 化。

    - ``call_stream()``：CLI/serve/多 Agent 共用的 SSE 事件流生成器；审批/前端
      暂停 return，下次传决策接续（内部映射 ``Command(resume=...)``）。
    - ``run_task()``：A2A 无头入口。
    - 编排细节见 ``GSagent/core/orchestration/``（state/graph/nodes）。
    """

    def __init__(
        self,
        chat_model: BaseChatModel,
        *,
        tools_registry: Optional[ToolRegistry] = None,
        max_steps: int = 20,
        enable_compaction: bool = True,
        compaction_threshold_ratio: float = 0.75,
        compaction_keep_last_n: int = 6,
        agent_id: str = "main",
        name: str = "",
        parent: Optional[BaseAgent] = None,
        knowledge_text: str = "",
        hitl_policy: Optional[HitlPolicy] = None,
        audit_log: Optional[AuditLog] = None,
        cost_estimator: Optional[CostEstimator] = None,
        record_usage: bool = True,
        event_emitter: Optional[EventEmitter] = None,
        tracer: Optional[Any] = None,
        input_guard: Optional[Any] = None,
        output_guard: Optional[Any] = None,
    ):
        super().__init__(
            agent_id,
            AgentRole.MAIN,
            name=name or "main",
            parent=parent,
            knowledge_text=knowledge_text,
        )
        self.chat_model = chat_model
        self.model_name = (
            getattr(chat_model, "model_name", "") or getattr(chat_model, "model", "") or "chat-model"
        )
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

        # 压缩桥接（BaseChatModel → 压缩模块 dict 接口）
        bridge = _CompactionBridge(chat_model)
        self._compactor = SessionCompactor(
            llm=bridge, keep_last_n=compaction_keep_last_n
        )
        self._limiter = ContextWindowLimiter(
            llm=bridge, threshold_ratio=compaction_threshold_ratio
        )
        self._graph = build_graph(self)

    def run_task(self, task: Task) -> Task:
        """无头模式：消费一轮 call_stream 并完成 Task（A2A 协议兼容）。"""
        text = task_input_text(task, self)
        messages = self.context_messages()
        if not messages:
            messages = build_chat_messages(
                ask=text,
                toolsets=[],
            )
        content = ""
        for event in self.call_stream(messages=messages, enable_tool_approval=False):
            if event.event == StreamEvents.ANSWER_END:
                content = event.data.get("content", "") or ""
        if content:
            set_task_state(task, TaskState.TASK_STATE_COMPLETED, content)
        else:
            set_task_state(task, TaskState.TASK_STATE_FAILED, "单 Agent 主循环未产出最终答复。")
        return task

    def call_stream(
        self,
        messages: List[Dict[str, Any]],
        enable_tool_approval: bool = False,
        tool_decisions: Optional[Dict[str, bool]] = None,
        frontend_tool_results: Optional[Dict[str, Any]] = None,
        request_context: Optional[Dict[str, Any]] = None,
        cancel_event: Any = None,
        tool_number_offset: int = 0,
        iteration_offset: int = 0,
    ) -> Generator[StreamMessage, None, None]:
        """以生成器形式运行 Agent 主循环（LangGraph 编排），产出 StreamMessage 事件。

        暂停恢复：传入 ``tool_decisions``/``frontend_tool_results`` 表示恢复——
        内部用 ``Command(resume=...)`` + checkpointer（thread_id 固定）断点续跑。
        """
        session_id = self._session_id(request_context)
        self._emit_agent_event(AgentEventType.AGENT_START, session_id)
        thread_id = (request_context or {}).get("thread_id") or self.agent_id
        config = {"configurable": {"thread_id": str(thread_id)}}
        if tool_decisions or frontend_tool_results:
            stream_input: Any = Command(
                resume={
                    "tool_decisions": dict(tool_decisions or {}),
                    "frontend_tool_results": dict(frontend_tool_results or {}),
                }
            )
        else:
            stream_input = self._build_initial_state(
                messages=messages,
                enable_tool_approval=enable_tool_approval,
                request_context=request_context,
                cancel_event=cancel_event,
                tool_number_offset=tool_number_offset,
                iteration_offset=iteration_offset,
            )
        try:
            yield from self._run_graph(stream_input, config, cancel_event)
        finally:
            self._emit_agent_event(AgentEventType.AGENT_END, session_id)

    def _emit_agent_event(self, event_type: AgentEventType, session_id: str) -> None:
        """任务级生命周期事件（AGENT_START/END）。"""
        _emit(self, event_type, session_id=session_id, message=event_type.value)

    @staticmethod
    def _build_initial_state(
        *,
        messages: List[Dict[str, Any]],
        enable_tool_approval: bool,
        request_context: Optional[Dict[str, Any]],
        cancel_event: Any,
        tool_number_offset: int,
        iteration_offset: int,
    ) -> Dict[str, Any]:
        """构造图初始状态（OpenAI dict 输入 → BaseMessage 图状态）。"""
        return {
            "messages": dict_to_messages(messages),
            "last_tool_calls": [],
            "prev_tool_calls": [],
            "no_progress_streak": 0,
            "iteration": int(iteration_offset),
            "tool_number": int(tool_number_offset),
            "terminated": None,
            "enable_tool_approval": bool(enable_tool_approval),
            "request_context": request_context,
            "cancel_event": cancel_event,
            "_events": [],
            "_stream_messages": [],
        }

    def _run_graph(
        self,
        stream_input: Any,
        config: Dict[str, Any],
        cancel_event: Any,
    ) -> Generator[StreamMessage, None, None]:
        """遍历 LangGraph 图，yield 节点事件；检测 interrupt（暂停）产暂停事件。"""
        tracer = self._tracer
        saw_terminal = False

        def _iter():
            nonlocal saw_terminal
            for chunk in self._graph.stream(
                stream_input, config, stream_mode="updates"
            ):
                if cancel_event is not None and cancel_event.is_set():
                    saw_terminal = True
                    yield StreamMessage(
                        event=StreamEvents.ERROR,
                        data={"error": "   cancelled by user."},
                    )
                    return
                if "__interrupt__" in chunk:
                    saw_terminal = True
                    for intr in chunk["__interrupt__"]:
                        value = intr.value
                        ev = (
                            StreamEvents.APPROVAL_REQUIRED
                            if value.get("type") == "approval"
                            else StreamEvents.FRONTEND_PAUSE
                        )
                        yield StreamMessage(event=ev, data=value)
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

        try:
            if tracer is not None:
                with tracer.start_as_current_span("invoke_agent") as span:
                    span.set_attribute("gen_ai.operation.name", "invoke_agent")
                    span.set_attribute("agent_id", self.agent_id)
                    yield from _iter()
            else:
                yield from _iter()
        except Exception as exc:  # noqa: BLE001 - 编排兜底
            logger.exception("LangGraph graph execution failed: %s", exc)
            yield StreamMessage(
                event=StreamEvents.ERROR,
                data={"error": f"LangGraph graph execution failed: {exc}"},
            )
            return
        if not saw_terminal:
            yield StreamMessage(
                event=StreamEvents.ERROR,
                data={
                    "error": "LangGraph graph finished without a terminal answer event.",
                },
            )

    def set_hitl_mode(self, mode: str) -> None:
        """运行时切换 HITL 模式（`/hitl <mode>`）。"""
        if self.hitl_policy is None:
            raise ValueError("No hitl_policy configured on this agent.")
        self.hitl_policy.set_mode(mode)
