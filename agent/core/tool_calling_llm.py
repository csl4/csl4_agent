"""Agent 核心主循环 —— 编排 LLM 调用与工具执行的 ToolCallingLLM 类。

主循环的每一轮迭代：
  ① 处理审批决策（tool_decisions 中 user_approved=True 的项）
  ② 处理前端工具结果（FRONTEND_PAUSE 之后恢复）
  ③ 检查是否需要压缩 → 上下文窗口接近上限时进行压缩
  ④ 携带 messages 和 tools 调用 LLM
  ⑤ 若无 tool_calls → 产出 ANSWER_END 并返回
  ⑥ 若有 tool_calls → 通过 ThreadPoolExecutor 并行执行
  ⑦ 处理 APPROVAL_REQUIRED 和 FRONTEND_PAUSE 状态
  ⑧ 将结果追加到 messages → 继续
"""

# ======================= 中文导览 =======================
# 本文件是【核心主循环】——把 LLM + ToolExecutor + Compactor + Limiter 组合成
# 一条「可被人类打断的生成器流水线」。
# 输入：messages(对话 dict 列表) + 可选 approval 决策 / 前端结果 / cancel_event；
# 输出：Generator[StreamMessage]，一边发生一边 yield 给前端（SSE 事件流）。
# 模型剪影（新手必读）：
#   主循环 = 交替「调 LLM → 并行执行工具 → 结果压回 messages → 再调 LLM」，
#   直到某轮 LLM 不再请求工具(tool_calls为空)，它就在那一刻综合历史所有工具输出，
#   产出最终答案 yield ANSWER_END。
# 设计理念：
#   ① 生成器而非 def：可流式渲染、可 cancel_event 打断。
#   ② 双暂停状态：APPROVAL_REQUIRED(等人审批) / FRONTEND_PAUSE(等前端执行)，
#      即 LangGraph interrupt() 实现的语义；下次调用凭 tool_decisions / frontend_tool_results 恢复。
#   ③ 底层对象(Tool/ToolInvokeContext)是引擎无关的 —— 同一套能被塞进手写循环或 LangGraph。
# =========================================================

import fnmatch
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Generator, List, Optional

from agent.core.llm import LLM, ModelResponse
from agent.core.models import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    ToolInvokeContext,
)
from agent.core.tool_executor import ToolExecutor
from agent.core.truncation.compaction import ConversationCompactor
from agent.core.truncation.input_context_window_limiter import ContextWindowLimiter
from agent.utils.stream import StreamEvents, StreamMessage

logger = logging.getLogger(__name__)


# ---- 行为对象：Agent 主循环 ----
# 输入：构造时注入 ToolExecutor + LLM + 压缩工具；调 call_stream() 送入 messages。
# 输出：call_stream() 返回生成器，产生 StreamMessage 事件流。
# 设计要点：持有 LLM + 工具执行 + 压缩(compactor+limiter) 三块，串成主循环；
#           整个 Agent 的「运行骨架」都在这里。
class ToolCallingLLM:
    """编排 LLM 调用与工具执行的核心 Agent 主循环。

    每轮迭代：
    1. 处理审批决策（user_approved=True 的项）
    2. 处理前端工具结果
    3. 检查是否需要压缩 → 压缩对话历史
    4. 携带 messages 和 tools 调用 LLM
    5. 若无 tool_calls → 产出 ANSWER_END 并返回
    6. 若有 tool_calls → 并行执行 → 追加结果 → 继续

    特殊状态：
    - APPROVAL_REQUIRED：暂停循环，等待用户决策（以 tool_decisions 恢复）
    - FRONTEND_PAUSE：暂停循环，等待前端执行（以 frontend_tool_results 恢复）
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        llm: LLM,
        max_steps: int = 20,
        tool_results_dir: str = "/tmp/agent_tool_results",
        enable_compaction: bool = True,
        compaction_threshold_ratio: float = 0.75,
        compaction_keep_last_n: int = 6,
    ):
        self.tool_executor = tool_executor
        self.llm = llm
        self.max_steps = max_steps
        self.tool_results_dir = tool_results_dir
        self.enable_compaction = enable_compaction
        self.compaction_threshold_ratio = compaction_threshold_ratio
        self.compaction_keep_last_n = compaction_keep_last_n

        # Initialize compaction utilities
        self._compactor = ConversationCompactor(
            llm=llm,
            keep_last_n=compaction_keep_last_n,
        )
        self._limiter = ContextWindowLimiter(
            llm=llm,
            threshold_ratio=compaction_threshold_ratio,
        )

    # 核心公开入口：跑完一轮主循环，返回生成器逐个吐 StreamMessage。
    #   call_stream 是【可暂停恢复】的：遇到审批/前端暂停会 return（不抛异常），
    #   下次以 tool_decisions / frontend_tool_results 为参再次调用即可接续。
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
        """以生成器形式运行 Agent 主循环，产出 StreamMessage 事件。

        参数:
            messages: 初始聊天消息（system + user）。
            enable_tool_approval: 若为 True，则对需要审批的工具检查审批。
            tool_decisions: 针对已暂停工具调用的预置审批决策。
                映射 tool_call_id -> bool（True = 已批准，False = 已拒绝）；
                对于单次调用，也接受 tool_name 键作为回退。
            frontend_tool_results: 前端执行工具后的结果。
                映射 tool_call_id → 结果数据。
            request_context: 可选字典，含 user_id、session_id 等。
            cancel_event: 用于取消的可选 threading.Event。
            tool_number_offset: 工具编号的起始偏移量。
            iteration_offset: 迭代计数的起始偏移量。

        产出:
            主循环每一步的 StreamMessage 事件。
        """
        if tool_decisions is None:
            tool_decisions = {}

        if frontend_tool_results is None:
            frontend_tool_results = {}

        working_messages = list(messages)
        tools = self.tool_executor.get_tools_as_openai()
        i = iteration_offset
        tool_number = tool_number_offset

        while i < self.max_steps:
            i += 1

            # Check for cancellation
            if cancel_event and cancel_event.is_set():
                yield StreamMessage(
                    event=StreamEvents.ERROR,
                    data={"error": "   cancelled by user."},
                )
                return

            # ① Process approval decisions — re-execute approved tools
            if tool_decisions:
                approved_count = self._execute_tool_decisions(
                    tool_decisions=tool_decisions,
                    working_messages=working_messages,
                    request_context=request_context,
                    tool_number=tool_number,
                )
                tool_number += approved_count
                tool_decisions = {}  # Clear after processing

            # ② Process frontend tool results — resume after FRONTEND_PAUSE
            if frontend_tool_results:
                self._process_frontend_tool_results(
                    frontend_tool_results=frontend_tool_results,
                    working_messages=working_messages,
                )
                frontend_tool_results = {}  # Clear after processing

            # ③ Check compaction needed
            if self.enable_compaction and not (i >= self.max_steps):
                if self._limiter.check_compaction_needed(working_messages, tools):
                    try:
                        current_tokens = self._limiter._estimate_tokens(
                            working_messages, tools
                        )
                    except Exception:
                        # Fall back to a rough estimate rather than crash the
                        # turn over a compaction metric. Extended messages
                        # should already be rare here (the JSON fallback in
                        # count_tokens normally masks this).
                        current_tokens = len(
                            json.dumps(working_messages, default=str)
                        ) // 4
                    max_tokens = self.llm.get_context_window_size()

                    yield StreamMessage(
                        event=StreamEvents.COMPACTION_START,
                        data={
                            "current_tokens": current_tokens,
                            "max_tokens": max_tokens,
                            "message_count": len(working_messages),
                        },
                    )

                    working_messages = self._compactor.compact(working_messages)

                    yield StreamMessage(
                        event=StreamEvents.COMPACTED,
                        data={
                            "new_message_count": len(working_messages),
                        },
                    )

            # ③.5 Safety net: inject denial results for any assistant
            # tool_calls that never got a response message (e.g. a sibling of
            # an approval-paused call, or an abandoned approval). Without this,
            # providers reject the next request with "tool_call_ids did not
            # have response messages".
            self._resolve_orphaned_tool_calls(working_messages)

            # ④ LLM call (streamed so answers render live; the provider
            # reassembles fragmented tool_calls into a complete response)
            is_last_step = i >= self.max_steps
            deltas = self.llm.completion_stream(
                messages=working_messages,
                tools=tools if not is_last_step else None,
                tool_choice="auto" if not is_last_step else "none",
            )
            response: ModelResponse
            while True:
                try:
                    delta = next(deltas)
                except StopIteration as stop:
                    response = stop.value
                    break
                if delta:
                    yield StreamMessage(
                        event=StreamEvents.ANSWER_DELTA,
                        data={"content": delta},
                    )

            # Append assistant message to history
            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            if response.content:
                assistant_msg["content"] = response.content
            if response.tool_calls:
                assistant_msg["tool_calls"] = response.tool_calls
            working_messages.append(assistant_msg)

            # ⑤ No tool_calls → answer complete
            if not response.tool_calls:
                yield StreamMessage(
                    event=StreamEvents.ANSWER_END,
                    data={
                        "content": response.content,
                        "messages": working_messages,
                        "num_llm_calls": i,
                        "usage": response.usage.model_dump() if response.usage else {},
                    },
                )
                return

            # ⑥ Execute tool calls in parallel
            # Yield START_TOOL for each tool call
            for tc in response.tool_calls:
                tool_number += 1
                yield StreamMessage(
                    event=StreamEvents.START_TOOL,
                    data={
                        "tool_call_id": tc.get("id", ""),
                        "tool_name": tc.get("function", {}).get("name", "unknown"),
                        "tool_number": tool_number,
                    },
                )

            with ThreadPoolExecutor(max_workers=16) as executor:
                futures = {}
                for tc in response.tool_calls:
                    future = executor.submit(
                        self._invoke_llm_tool_call,
                        tool_call=tc,
                        request_context=request_context,
                        enable_tool_approval=enable_tool_approval,
                        tool_decisions=tool_decisions,
                    )
                    futures[future] = tc

                # Drain ALL futures before pausing: siblings in the same batch
                # must still get their result messages, otherwise their
                # tool_call_ids are orphaned and the next LLM call fails with
                # "tool_call_ids did not have response messages".
                approval_pauses: List[ToolCallResult] = []
                frontend_pause: Optional[ToolCallResult] = None
                for future in as_completed(futures):
                    result = future.result()

                    if isinstance(result, ToolCallResult):
                        # ⑦ Handle special states - remember every pause, keep
                        # collecting sibling results, yield the pause(s) after
                        # the batch completes.
                        if result.result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
                            approval_pauses.append(result)
                            continue

                        if result.result.status == StructuredToolResultStatus.FRONTEND_PAUSE:
                            if frontend_pause is None:
                                frontend_pause = result
                            continue

                        # ⑧ Normal result -> append to messages
                        working_messages.append(result.to_llm_message())
                        yield StreamMessage(
                            event=StreamEvents.TOOL_RESULT,
                            data={
                                "tool_name": result.tool_name,
                                "tool_call_id": result.tool_call_id,
                                "status": result.result.status.value,
                                "execution_time_ms": result.execution_time_ms,
                                "invocation": result.result.invocation,
                                "return_code": result.result.return_code,
                            },
                        )

                    elif isinstance(result, StreamMessage):
                        # Error during tool execution
                        yield result

            if approval_pauses:
                # Surface EVERY approval-needed call in the batch: the event
                # carries a pending_approvals list (plus the legacy single-call
                # fields pointing at the first) so the frontend can prompt for
                # each one and resume with a decision per tool_call_id.
                first = approval_pauses[0]
                yield StreamMessage(
                    event=StreamEvents.APPROVAL_REQUIRED,
                    data={
                        "tool_name": first.tool_name,
                        "tool_call_id": first.tool_call_id,
                        "params": first.result.params,
                        "reason": first.result.error,
                        "prefixes_to_save": first.result.prefixes_to_save or [],
                        "pending_approvals": [
                            {
                                "tool_name": pr.tool_name,
                                "tool_call_id": pr.tool_call_id,
                                "params": pr.result.params,
                                "reason": pr.result.error,
                                "prefixes_to_save": pr.result.prefixes_to_save or [],
                            }
                            for pr in approval_pauses
                        ],
                        "messages": working_messages,
                        "num_llm_calls": i,
                    },
                )
                return

            if frontend_pause is not None:
                yield StreamMessage(
                    event=StreamEvents.FRONTEND_PAUSE,
                    data={
                        "tool_name": frontend_pause.tool_name,
                        "tool_call_id": frontend_pause.tool_call_id,
                        "messages": working_messages,
                        "num_llm_calls": i,
                    },
                )
                return

        # ⑨ Loop exhausted (max_steps reached) without a tool-free answer.
        # Some models ignore tool_choice="none" on the final step, so the last
        # response may still carry tool_calls. Never return silently here -
        # the caller would see no terminal event at all. Prefer whatever
        # content the final assistant message produced, else raise an error.
        last_assistant = next(
            (m for m in reversed(working_messages) if m.get("role") == "assistant"),
            None,
        )
        fallback_content = (last_assistant or {}).get("content") or ""
        if fallback_content:
            yield StreamMessage(
                event=StreamEvents.ANSWER_END,
                data={
                    "content": fallback_content,
                    "messages": working_messages,
                    "num_llm_calls": i,
                    "usage": {},
                    "max_steps_reached": True,
                },
            )
        else:
            yield StreamMessage(
                event=StreamEvents.ERROR,
                data={
                    "error": (
                        f"Max steps ({self.max_steps}) reached without a final "
                        "answer. Increase the step limit or simplify the task."
                    ),
                    "messages": working_messages,
                },
            )

    def _invoke_llm_tool_call(
            self,
            tool_call: Dict[str, Any],
            request_context: Optional[Dict[str, Any]],
            enable_tool_approval: bool,
            tool_decisions: Dict[str, bool],
    ) -> Any:
        """执行单个 LLM 工具调用。

        根据该工具是否已获用户批准，构建带正确 user_approved 标志的
        ToolInvokeContext。

        返回:
            成功时返回 ToolCallResult，出错时返回 StreamMessage。
        """
        tool_call_id = tool_call.get("id", "")
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")

        try:
            params = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid JSON arguments for tool '{tool_name}'",
                ),
            )

        # Lazy initialization
        error = self.tool_executor.ensure_toolset_initialized(tool_name)
        if error:
            return StreamMessage(
                event=StreamEvents.ERROR,
                data={"error": error, "tool_name": tool_name},
            )

        # Determine if this tool call has been approved by the user.
        # A decision for this call id means the user was prompted for THIS
        # call (dynamic approval via Tool.requires_approval, e.g. the bash
        # toolset) - it must be honored regardless of toolset-level patterns.
        # Decisions are keyed by tool_call_id; tool_name is accepted as a
        # fallback for callers that don't distinguish parallel calls.
        user_approved = False
        if enable_tool_approval:
            decision = tool_decisions.get(tool_call_id)
            if decision is None:
                decision = tool_decisions.get(tool_name)
            if decision is not None:
                if decision:
                    user_approved = True
                else:
                    # Explicitly denied
                    return ToolCallResult(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        result=StructuredToolResult(
                            status=StructuredToolResultStatus.ERROR,
                            error=f"Tool '{tool_name}' was denied by the user",
                            params=params,
                        ),
                    )
            else:
                # No decision yet. Tools gated by toolset-level approval
                # patterns pause here; tools with dynamic approval (bash)
                # fall through to Tool.invoke -> requires_approval().
                toolset_name = self.tool_executor.get_toolset_name(tool_name)
                toolset = self._get_toolset_by_name(toolset_name)
                if toolset and self._tool_requires_approval(tool_name, toolset):
                    # Not yet decided → need approval
                    return ToolCallResult(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        result=StructuredToolResult(
                            status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                            params=params,
                        ),
                    )

        # Build the ToolInvokeContext with taint tracking
        invoke_context = ToolInvokeContext(
            user_approved=user_approved,
            llm=self.llm,
            max_token_count=8000,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            request_context=request_context,
            toolset=self.tool_executor.get_toolset_for(tool_name),
        )

        # Execute the tool with the proper context
        return self.tool_executor.execute_tool(
            tool_name=tool_name,
            params=params,
            context=invoke_context,
            tool_call_id=tool_call_id,
        )

    @staticmethod
    def _answered_tool_call_ids(messages: List[Dict[str, Any]]) -> set:
        """返回已有响应消息的 tool_call_id 集合。"""
        return {
            msg.get("tool_call_id")
            for msg in messages
            if msg.get("role") == "tool" and msg.get("tool_call_id")
        }

    @staticmethod
    def _denial_result(tool_call_id: str, tool_name: str, error: str) -> ToolCallResult:
        """构建一个用于回应未执行/被拒绝调用的 ERROR 类型 ToolCallResult。

        统一了拒绝消息的形态，使所有「未执行却要回应 tool_call_id」的路径
        都能产出完全相同、对供应商安全的工具消息（经由 ToolCallResult.to_llm_message()）。
        """
        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error,
            ),
        )

    def _resolve_orphaned_tool_calls(
        self, messages: List[Dict[str, Any]]
    ) -> None:
        """为没有响应的 assistant tool_calls 注入拒绝型工具结果。

        当 assistant 发起了工具调用、但对话从未记录到对应的工具结果时，
        该调用即视为「孤儿（orphaned）」。这发生在：一批调用因审批而暂停、
        其中的兄弟调用被搁置未应答；或某个待处理的审批被放弃。供应商
        （OpenAI/DeepSeek/Anthropic）要求每个 tool_call_id 都有响应消息，
        否则下一次 LLM 调用会直接失败。这里将其按已取消处理，以便对话继续。
        """
        answered_ids = self._answered_tool_call_ids(messages)

        # Walk from the end so insertions don't shift indices we haven't visited.
        for i in reversed(range(len(messages))):
            msg = messages[i]
            if msg.get("role") != "assistant" or not msg.get("tool_calls"):
                continue
            insert_offset = 1
            for tool_call in msg.get("tool_calls", []):
                tool_call_id = tool_call.get("id")
                if not tool_call_id or tool_call_id in answered_ids:
                    continue
                function = tool_call.get("function") or {}
                tool_name = function.get("name") or "unknown"
                result = self._denial_result(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error="Tool execution was cancelled: no response was "
                    "recorded for this tool call.",
                )
                messages.insert(i + insert_offset, result.to_llm_message())
                answered_ids.add(tool_call_id)
                insert_offset += 1
                logger.info(
                    f"Injected cancellation result for orphaned tool call "
                    f"{tool_call_id} ({tool_name})"
                )

    def _execute_tool_decisions(
        self,
        tool_decisions: Dict[str, bool],
        working_messages: List[Dict[str, Any]],
        request_context: Optional[Dict[str, Any]],
        tool_number: int,
    ) -> int:
        """处理暂停批次对应的用户决策。

        已批准的调用以 user_approved=True 重新执行；明确拒绝的调用会得到
        一条拒绝型工具消息，从而其 tool_call_id 得到回应。两者都保证对话
        对下一次 LLM 调用仍然有效。

        参数:
            tool_decisions: 映射 tool_call_id（优先）或 tool_name -> bool。
            working_messages: 当前对话消息。
            request_context: 单次请求的上下文字典。
            tool_number: 当前工具编号偏移量。

        返回:
            已执行的已批准工具数量。
        """
        # Calls that already have a response must never be re-executed (a
        # duplicate tool_call_id response would make providers reject the
        # next request).
        answered_ids = self._answered_tool_call_ids(working_messages)

        executed = 0
        for msg in reversed(working_messages):
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                continue

            for tc in tool_calls:
                tc_id = tc.get("id", "")
                if not tc_id or tc_id in answered_ids:
                    continue
                decision = tool_decisions.get(tc_id)
                if decision is None:
                    decision = tool_decisions.get(
                        tc.get("function", {}).get("name", "")
                    )
                if decision is None:
                    continue

                if decision:
                    result = self._invoke_llm_tool_call(
                        tool_call=tc,
                        request_context=request_context,
                        enable_tool_approval=True,
                        tool_decisions={tc_id: True},
                    )
                    if isinstance(result, ToolCallResult):
                        working_messages.append(result.to_llm_message())
                        executed += 1
                else:
                    # Explicitly denied: record a denial result so the id is
                    # answered (same shape as every other denial) and the model
                    # can react to the refusal.
                    tool_name = tc.get("function", {}).get("name", "unknown")
                    working_messages.append(
                        self._denial_result(
                            tool_call_id=tc_id,
                            tool_name=tool_name,
                            error="User denied approval for this tool call.",
                        ).to_llm_message()
                    )

        return executed

    def _process_frontend_tool_results(
        self,
        frontend_tool_results: Dict[str, Any],
        working_messages: List[Dict[str, Any]],
    ) -> None:
        """处理前端工具结果，以在 FRONTEND_PAUSE 之后恢复。

        将前端执行结果以工具消息的形式插入对话历史。

        参数:
            frontend_tool_results: 映射 tool_call_id → 结果数据。
            working_messages: 当前对话消息。
        """
        for tool_call_id, result_data in frontend_tool_results.items():
            content = (
                json.dumps(result_data, ensure_ascii=False, default=str)
                if not isinstance(result_data, str)
                else result_data
            )
            working_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })

    def _get_toolset_by_name(self, name: Optional[str]) -> Any:
        """按名称查找 toolset。"""
        if not name:
            return None
        for ts in self.tool_executor.toolsets:
            if ts.name == name:
                return ts
        return None

    @staticmethod
    def _tool_requires_approval(tool_name: str, toolset: Any) -> bool:
        """根据 toolset 配置检查某工具是否需要审批。

        支持在 approval_required_tools 中使用 fnmatch 风格的 glob 模式。
        """
        for pattern in (toolset.approval_required_tools or []):
            if fnmatch.fnmatch(tool_name, pattern):
                return True
        return False