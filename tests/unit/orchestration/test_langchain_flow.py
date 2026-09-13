"""US1 集成测试（T011）：langchain 化编排全流 + 审批 interrupt 恢复。

FakeChatLLM 离线验证：
- Human → AIMessage(tool_calls) → @tool 执行 → ToolMessage → AIMessage(content) → answer_end
- 审批工具：interrupt() 暂停 → APPROVAL_REQUIRED → 下次 tool_decisions 恢复执行
"""

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents
from tests.helpers import FakeChatLLM


@tool
def echo(text: str) -> str:
    """echo back the text"""
    return f"echo:{text}"


def _usage(p=3, c=2):
    return {
        "token_usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    }


class TestLangchainFlow:
    def test_tool_roundtrip_full_flow(self):
        """Human → AIMessage(tool_calls) → ToolMessage → 回答，add_messages 配对。"""
        registry = ToolRegistry()
        registry.register(echo)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "echo", "args": {"text": "hi"}, "id": "c1", "type": "tool_call"}
                    ],
                    response_metadata=_usage(5, 3),
                ),
                AIMessage(content="done after echo", response_metadata=_usage(10, 4)),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm,
            tools_registry=registry,
            max_steps=5,
            enable_compaction=False,
        )
        events = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        end = [e for e in events if e.event == StreamEvents.ANSWER_END]
        assert end, "应有 ANSWER_END"
        assert "done after echo" in end[-1].data["content"]
        # 消息快照含 ToolMessage（工具结果已配对）
        msgs = end[-1].data["messages"]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert tool_msgs and "echo:hi" in tool_msgs[0]["content"]
        assert tool_msgs[0]["tool_call_id"] == "c1"

    def test_approval_interrupt_then_resume(self):
        """审批工具：interrupt 暂停 → APPROVAL_REQUIRED → tool_decisions 恢复执行。"""
        registry = ToolRegistry()
        registry.register(echo, requires_approval=True)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "echo", "args": {"text": "x"}, "id": "c2", "type": "tool_call"}
                    ],
                    response_metadata=_usage(5, 3),
                ),
                AIMessage(content="approved done", response_metadata=_usage(10, 4)),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm,
            tools_registry=registry,
            max_steps=5,
            enable_compaction=False,
        )
        events1 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        approval = [e for e in events1 if e.event == StreamEvents.APPROVAL_REQUIRED]
        assert approval, "应有 APPROVAL_REQUIRED 事件"
        assert not any(e.event == StreamEvents.ANSWER_END for e in events1)

        # 恢复：批准后接续（同 agent 实例，thread_id 固定断点续跑）
        events2 = list(
            agent.call_stream(
                messages=[{"role": "user", "content": "run"}],
                tool_decisions={"c2": True},
            )
        )
        end = [e for e in events2 if e.event == StreamEvents.ANSWER_END]
        assert end, "恢复后应有 ANSWER_END"
        assert "approved done" in end[-1].data["content"]
