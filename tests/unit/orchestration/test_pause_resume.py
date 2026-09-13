"""编排暂停/恢复测试（langchain 化版）：审批/前端 interrupt 断点恢复。

FakeChatLLM 离线打桩。验证（SC-005，FR-004）：
- 审批暂停 → APPROVAL_REQUIRED → tool_decisions 接续 → 断点续跑
- 审批拒绝 → 错误回填 → 继续
- 前端暂停 → FRONTEND_PAUSE → frontend_tool_results 接续
"""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents
from tests.helpers import FakeChatLLM


@tool
def echo_tool(text: str) -> str:
    """echo test tool"""
    return f"echo:{text}"


@tool
def frontend_tool() -> dict:
    """frontend pause tool（返回前端暂停信号）"""
    return {"__frontend_pause__": True}


def _usage(p=5, c=3):
    return {
        "token_usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    }


class TestPauseResume:
    def test_approval_pause_then_resume(self):
        """审批暂停 → APPROVAL_REQUIRED → 下次 tool_decisions 接续 → ANSWER_END。"""
        reg = ToolRegistry()
        reg.register(echo_tool, requires_approval=True)
        tc_id = "c1"
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "echo_tool", "args": {"text": "hi"}, "id": tc_id, "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="done after approval", response_metadata=_usage()),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False
        )

        events1 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        approval = [e for e in events1 if e.event == StreamEvents.APPROVAL_REQUIRED]
        assert approval, "应有 APPROVAL_REQUIRED"
        assert not any(e.event == StreamEvents.ANSWER_END for e in events1)

        events2 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}], tool_decisions={tc_id: True}))
        end = [e for e in events2 if e.event == StreamEvents.ANSWER_END]
        assert end, "恢复后应有 ANSWER_END"
        assert "done after approval" in end[-1].data["content"]
        # 工具结果已入对话
        assert any(
            m.get("role") == "tool" and m.get("tool_call_id") == tc_id
            for m in end[-1].data["messages"]
        )

    def test_approval_deny_returns_error_tool_result(self):
        """审批拒绝 → 错误回填，不执行工具，继续完成。"""
        reg = ToolRegistry()
        reg.register(echo_tool, requires_approval=True)
        tc_id = "c2"
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "echo_tool", "args": {"text": "x"}, "id": tc_id, "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="proceeded without tool", response_metadata=_usage()),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False
        )
        list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        events2 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}], tool_decisions={tc_id: False}))
        end = [e for e in events2 if e.event == StreamEvents.ANSWER_END]
        assert end and "proceeded without tool" in end[-1].data["content"]

    def test_frontend_pause_then_resume(self):
        """前端暂停 → FRONTEND_PAUSE → 下次 frontend_tool_results 接续 → ANSWER_END。"""
        reg = ToolRegistry()
        reg.register(frontend_tool)
        tc_id = "c3"
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "frontend_tool", "args": {}, "id": tc_id, "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="after frontend", response_metadata=_usage()),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False
        )
        events1 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        fp = [e for e in events1 if e.event == StreamEvents.FRONTEND_PAUSE]
        assert fp, "应有 FRONTEND_PAUSE 事件"
        assert not any(e.event == StreamEvents.ANSWER_END for e in events1)

        events2 = list(
            agent.call_stream(
                messages=[{"role": "user", "content": "run"}],
                frontend_tool_results={tc_id: {"ok": True}},
            )
        )
        end = [e for e in events2 if e.event == StreamEvents.ANSWER_END]
        assert end and "after frontend" in end[-1].data["content"]
