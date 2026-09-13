"""编排回归门禁测试：langchain 化后 call_stream 外部契约不变。

宪法 IV / CLAUDE.md：离线用 FakeChatLLM 打桩，不走 HTTP。
断言：非暂停路径的 StreamMessage 序列与 langchain 化前一致（contracts §1）。
"""

import threading

from langchain_core.messages import AIMessage

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents
from tests.helpers import FakeChatLLM


def _usage(p=3, c=2):
    return {
        "token_usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    }


def _agent(chat_model, max_steps=5):
    return ToolCallingLLM(
        chat_model=chat_model,
        tools_registry=ToolRegistry(),
        max_steps=max_steps,
        enable_compaction=False,
    )


def _tool_call(name="nope_tool", tc_id="c1", args=None):
    return {"name": name, "args": args or {}, "id": tc_id, "type": "tool_call"}


class TestCallStreamContract:
    def test_simple_answer_event_sequence(self):
        """一次无工具 LLM 调用 → delta → usage → answer_end。"""
        llm = FakeChatLLM(
            responses=[AIMessage(content="hello world", response_metadata=_usage())]
        )
        events = list(_agent(llm).call_stream(messages=[{"role": "user", "content": "hi"}]))
        seq = [e.event for e in events]
        assert seq == [
            StreamEvents.ANSWER_DELTA,
            StreamEvents.USAGE,
            StreamEvents.ANSWER_END,
        ]
        assert events[-1].data["content"] == "hello world"
        assert events[-1].data["num_llm_calls"] == 1

    def test_tool_call_error_then_answer(self):
        """工具调用（工具缺失）→ error 事件 → 下轮 LLM → answer_end。"""
        llm = FakeChatLLM(
            responses=[
                AIMessage(content="", tool_calls=[_tool_call()], response_metadata=_usage(5, 3)),
                AIMessage(content="done after tool", response_metadata=_usage(10, 4)),
            ]
        )
        events = list(_agent(llm).call_stream(messages=[{"role": "user", "content": "run"}]))
        seq = [e.event for e in events]
        assert StreamEvents.START_TOOL in seq
        assert StreamEvents.ERROR in seq  # 工具 not found → error
        assert seq[-1] == StreamEvents.ANSWER_END
        assert events[-1].data["content"] == "done after tool"

    def test_answer_end_contains_usage(self):
        """ANSWER_END 携带完整 usage（含 token 与估算 cost）。"""
        llm = FakeChatLLM(
            responses=[AIMessage(content="x", response_metadata=_usage(3, 2))]
        )
        events = list(_agent(llm).call_stream(messages=[{"role": "user", "content": "hi"}]))
        end = [e for e in events if e.event == StreamEvents.ANSWER_END][0]
        usage = end.data["usage"]
        assert usage["prompt_tokens"] == 3
        assert usage["completion_tokens"] == 2
        assert "model" in usage
        assert "estimated_cost" in usage

    def test_max_steps_without_answer_errors(self):
        """恒返回工具调用直至熔断 → ERROR（无 fallback content）。"""
        tc = AIMessage(content="", tool_calls=[_tool_call()], response_metadata=_usage(5, 3))
        llm = FakeChatLLM(responses=[tc, tc, tc, tc])
        events = list(_agent(llm, max_steps=2).call_stream(messages=[{"role": "user", "content": "loop"}]))
        assert any(e.event == StreamEvents.ERROR for e in events)
        assert not any(e.event == StreamEvents.ANSWER_END for e in events)

    def test_cancel_event_terminates(self):
        """cancel_event 已设置 → 立即 ERROR（cancelled by user）。"""
        llm = FakeChatLLM(
            responses=[AIMessage(content="x", response_metadata=_usage())]
        )
        cancel = threading.Event()
        cancel.set()
        events = list(
            _agent(llm).call_stream(
                messages=[{"role": "user", "content": "hi"}], cancel_event=cancel
            )
        )
        assert len(events) == 1
        assert events[0].event == StreamEvents.ERROR
        assert "cancelled" in events[0].data["error"]
