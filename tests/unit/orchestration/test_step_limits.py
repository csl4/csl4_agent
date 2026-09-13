"""编排步数熔断与死循环检测测试（langchain 化版）。

宪法 2.3（步数熔断）+ 宪法 10.1（连续 3 步相同调用 → 终止）。
FakeChatLLM 离线打桩。
"""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents
from tests.helpers import FakeChatLLM


@tool
def echo_tool(x: int = 1) -> str:
    """echo test tool"""
    return "echo"


def _registry():
    reg = ToolRegistry()
    reg.register(echo_tool)
    return reg


def _usage(p=5, c=3):
    return {
        "token_usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    }


def _tc(tc_id="c1", name="echo_tool", args=None):
    return {"name": name, "args": args or {"x": 1}, "id": tc_id, "type": "tool_call"}


class TestStepLimits:
    def test_max_steps_with_fallback_content_returns_partial(self):
        """最后一步仍有 tool_calls（带 content）→ 熔断 ANSWER_END(max_steps_reached=True)。"""
        resp = AIMessage(content="partial result", tool_calls=[_tc()], response_metadata=_usage())
        llm = FakeChatLLM(responses=[resp, resp, resp, resp])
        agent = ToolCallingLLM(
            chat_model=llm,
            tools_registry=_registry(),
            max_steps=2,
            enable_compaction=False,
        )
        events = list(agent.call_stream(messages=[{"role": "user", "content": "loop"}]))
        ends = [e for e in events if e.event == StreamEvents.ANSWER_END]
        assert ends, "熔断应返回部分结果"
        assert ends[-1].data.get("max_steps_reached") is True
        assert "partial result" in ends[-1].data["content"]

    def test_dead_loop_same_tool_calls_terminates(self):
        """连续 3 步相同工具调用 → 死循环终止（不无限循环，提前结束）。"""
        same = _tc()
        resp = AIMessage(content="", tool_calls=[same], response_metadata=_usage())
        llm = FakeChatLLM(responses=[resp] * 12)
        agent = ToolCallingLLM(
            chat_model=llm,
            tools_registry=_registry(),
            max_steps=10,  # 高于死循环阈值，验证提前终止
            enable_compaction=False,
        )
        events = list(agent.call_stream(messages=[{"role": "user", "content": "loop"}]))
        assert llm.calls < 10, f"死循环未提前终止，LLM 被调用 {llm.calls} 次"
        assert any(e.event == StreamEvents.ERROR for e in events) or any(
            e.event == StreamEvents.ANSWER_END and e.data.get("max_steps_reached")
            for e in events
        )
