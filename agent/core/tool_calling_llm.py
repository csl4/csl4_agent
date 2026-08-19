"""Agent core loop — the ToolCallingLLM class that orchestrates LLM + tool execution.

Each iteration of the main loop:
  ① Process approval decisions (tool_decisions with user_approved=True)
  ② Process frontend tool results (resume after FRONTEND_PAUSE)
  ③ Check compaction needed → compact if context window is near limit
  ④ Call LLM with messages and tools
  ⑤ If no tool_calls → yield ANSWER_END and return
  ⑥ If tool_calls → execute in parallel via ThreadPoolExecutor
  ⑦ Handle APPROVAL_REQUIRED and FRONTEND_PAUSE states
  ⑧ Append results to messages → continue
"""

import fnmatch
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Generator, List, Optional

from agent.core.llm import LLM, ModelResponse
from agent.core.models import (
    StructuredToolResultStatus,
    ToolCallResult,
    ToolInvokeContext,
)
from agent.core.tool_executor import ToolExecutor
from agent.core.truncation.compaction import ConversationCompactor
from agent.core.truncation.input_context_window_limiter import ContextWindowLimiter
from agent.utils.stream import StreamEvents, StreamMessage

logger = logging.getLogger(__name__)


class ToolCallingLLM:
    """Core agent loop that orchestrates LLM calls and tool execution.

    Each iteration:
    1. Process approval decisions (with user_approved=True)
    2. Process frontend tool results
    3. Check compaction needed → compact conversation history
    4. Call LLM with messages and tools
    5. If no tool_calls → yield ANSWER_END and return
    6. If tool_calls → execute in parallel → append results → continue

    Special states:
    - APPROVAL_REQUIRED: pause loop, wait for user decision (resume with tool_decisions)
    - FRONTEND_PAUSE: pause loop, wait for frontend execution (resume with frontend_tool_results)
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
        """Run the agent loop as a generator yielding StreamMessage events.

        Args:
            messages: Initial chat messages (system + user).
            enable_tool_approval: If True, check approval for tools that require it.
            tool_decisions: Pre-made approval decisions for tools.
                Maps tool_name → bool (True = approved, False = denied).
            frontend_tool_results: Results from frontend-executed tools.
                Maps tool_call_id → result data.
            request_context: Optional dict with user_id, session_id, etc.
            cancel_event: Optional threading.Event for cancellation.
            tool_number_offset: Starting offset for tool numbering.
            iteration_offset: Starting offset for iteration counting.

        Yields:
            StreamMessage events for each step of the loop.
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
                    current_tokens = self._limiter._estimate_tokens(
                        working_messages, tools
                    )
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

            # ④ LLM call (non-streaming — need complete tool_calls for decisions)
            is_last_step = i >= self.max_steps
            response = self.llm.completion(
                messages=working_messages,
                tools=tools if not is_last_step else None,
                tool_choice="auto" if not is_last_step else "none",
                stream=False,
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

                for future in as_completed(futures):
                    result = future.result()

                    if isinstance(result, ToolCallResult):
                        # ⑦ Handle special states
                        if result.result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
                            yield StreamMessage(
                                event=StreamEvents.APPROVAL_REQUIRED,
                                data={
                                    "tool_name": result.tool_name,
                                    "tool_call_id": result.tool_call_id,
                                    "params": result.result.params,
                                    "messages": working_messages,
                                    "num_llm_calls": i,
                                },
                            )
                            return

                        if result.result.status == StructuredToolResultStatus.FRONTEND_PAUSE:
                            yield StreamMessage(
                                event=StreamEvents.FRONTEND_PAUSE,
                                data={
                                    "tool_name": result.tool_name,
                                    "tool_call_id": result.tool_call_id,
                                    "messages": working_messages,
                                    "num_llm_calls": i,
                                },
                            )
                            return

                        # ⑧ Normal result → append to messages
                        working_messages.append(result.to_llm_message())
                        yield StreamMessage(
                            event=StreamEvents.TOOL_RESULT,
                            data={
                                "tool_name": result.tool_name,
                                "tool_call_id": result.tool_call_id,
                                "status": result.result.status.value,
                                "execution_time_ms": result.execution_time_ms,
                            },
                        )

                    elif isinstance(result, StreamMessage):
                        # Error during tool execution
                        yield result

    def _invoke_llm_tool_call(
        self,
        tool_call: Dict[str, Any],
        request_context: Optional[Dict[str, Any]],
        enable_tool_approval: bool,
        tool_decisions: Dict[str, bool],
    ) -> Any:
        """Execute a single LLM tool call.

        Builds a ToolInvokeContext with the proper user_approved flag
        based on whether the tool was approved by the user.

        Returns:
            ToolCallResult on success, StreamMessage on error.
        """
        tool_call_id = tool_call.get("id", "")
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")

        import json

        try:
            params = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=ToolCallResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    result=StructuredToolResultStatus.ERROR,
                ).result,
            )

        # Lazy initialization
        error = self.tool_executor.ensure_toolset_initialized(tool_name)
        if error:
            return StreamMessage(
                event=StreamEvents.ERROR,
                data={"error": error, "tool_name": tool_name},
            )

        # Determine if this tool call has been approved by the user
        user_approved = False
        if enable_tool_approval:
            toolset_name = self.tool_executor.get_toolset_name(tool_name)
            toolset = self._get_toolset_by_name(toolset_name)
            if toolset and self._tool_requires_approval(tool_name, toolset):
                if tool_name in tool_decisions:
                    if tool_decisions.get(tool_name, False):
                        user_approved = True
                    else:
                        # Explicitly denied
                        return ToolCallResult(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            result=ToolCallResult(
                                tool_call_id=tool_call_id,
                                tool_name=tool_name,
                                result=StructuredToolResultStatus.ERROR,
                            ).result,
                        )
                else:
                    # Not yet decided → need approval
                    return ToolCallResult(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        result=ToolCallResult(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            result=StructuredToolResultStatus.APPROVAL_REQUIRED,
                        ).result,
                    )

        # Build the ToolInvokeContext with taint tracking
        invoke_context = ToolInvokeContext(
            user_approved=user_approved,
            llm=self.llm,
            max_token_count=8000,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            request_context=request_context,
            toolset=self.tool_executor._tool_to_toolset.get(tool_name),
        )

        # Execute the tool with the proper context
        return self.tool_executor.execute_tool(
            tool_name=tool_name,
            params=params,
            context=invoke_context,
            tool_call_id=tool_call_id,
        )

    def _execute_tool_decisions(
        self,
        tool_decisions: Dict[str, bool],
        working_messages: List[Dict[str, Any]],
        request_context: Optional[Dict[str, Any]],
        tool_number: int,
    ) -> int:
        """Process approved tool decisions.

        Re-executes tools that were approved by the user with
        user_approved=True in the ToolInvokeContext.

        Args:
            tool_decisions: Maps tool_name → bool.
            working_messages: Current conversation messages.
            request_context: Per-request context dict.
            tool_number: Current tool number offset.

        Returns:
            Number of approved tools executed.
        """
        executed = 0
        for tool_name, approved in tool_decisions.items():
            if not approved:
                continue

            # Find the tool call in the last assistant message
            for msg in reversed(working_messages):
                if msg.get("role") != "assistant":
                    continue
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    if tc.get("function", {}).get("name") == tool_name:
                        result = self._invoke_llm_tool_call(
                            tool_call=tc,
                            request_context=request_context,
                            enable_tool_approval=True,
                            tool_decisions={tool_name: True},
                        )
                        if isinstance(result, ToolCallResult):
                            working_messages.append(result.to_llm_message())
                            executed += 1
                        break

        return executed

    def _process_frontend_tool_results(
        self,
        frontend_tool_results: Dict[str, Any],
        working_messages: List[Dict[str, Any]],
    ) -> None:
        """Process frontend tool results to resume after FRONTEND_PAUSE.

        Inserts frontend execution results as tool messages into the
        conversation history.

        Args:
            frontend_tool_results: Maps tool_call_id → result data.
            working_messages: Current conversation messages.
        """
        for tool_call_id, result_data in frontend_tool_results.items():
            import json

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
        """Find a toolset by name."""
        if not name:
            return None
        for ts in self.tool_executor.toolsets:
            if ts.name == name:
                return ts
        return None

    @staticmethod
    def _tool_requires_approval(tool_name: str, toolset: Any) -> bool:
        """Check if a tool requires approval based on toolset config.

        Supports fnmatch-style glob patterns in approval_required_tools.
        """
        for pattern in (toolset.approval_required_tools or []):
            if fnmatch.fnmatch(tool_name, pattern):
                return True
        return False