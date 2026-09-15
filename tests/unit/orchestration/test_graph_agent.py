"""GraphAgent 测试（纯 langgraph 重构，Phase 1）。

FakeChatLLM 离线打桩。验证新图（ToolNode + 审批下沉）：
- 无工具路径 → ANSWER_END
- 工具路径：AIMessage(tool_calls) → ToolNode 执行 → ToolMessage → 回答
- 审批路径：PauseRequest(approval) → per-id resume 批准 → 执行 → 回答
- 审批拒绝：不执行 → 回答
- checkpointer 可注入（SqliteSaver / InMemorySaver）
"""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from GSagent.core.agents.graph_agent import GraphAgent, PauseRequest
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents, StreamMessage
from tests.helpers import FakeChatLLM

EXECUTED: list = []


@tool
def echo_tool(text: str) -> str:
    """echo test tool"""
    EXECUTED.append(("echo_tool", text))
    return f"echo:{text}"


@tool
def risky_tool(text: str) -> str:
    """需审批工具"""
    EXECUTED.append(("risky_tool", text))
    return f"risky:{text}"


def _usage(p=5, c=3):
    return {"token_usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}}


def _new_agent(reg: ToolRegistry, llm: FakeChatLLM, **kw) -> GraphAgent:
    return GraphAgent(chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False, **kw)


class TestGraphAgent:
    def test_no_tool_path_answers(self):
        """无工具路径：直接回答，产出 ANSWER_DELTA/ANSWER_END。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        llm = FakeChatLLM(responses=[AIMessage(content="hello there", response_metadata=_usage())])
        agent = _new_agent(reg, llm)
        events = list(agent.stream(messages=[{"role": "user", "content": "hi"}], session_id="ga-1"))
        ends = [e for e in events if isinstance(e, StreamMessage) and e.event == StreamEvents.ANSWER_END]
        assert ends, "应有 ANSWER_END"
        assert "hello there" in ends[-1].data["content"]

    def test_tool_path_executes(self):
        """工具路径：AIMessage(tool_calls) → ToolNode → ToolMessage → 回答。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(echo_tool)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "echo_tool", "args": {"text": "hi"}, "id": "c1", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="echoed", response_metadata=_usage()),
            ]
        )
        agent = _new_agent(reg, llm)
        events = list(agent.stream(messages=[{"role": "user", "content": "run"}], session_id="ga-2"))
        assert ("echo_tool", "hi") in EXECUTED, "工具应执行"
        ends = [e for e in events if isinstance(e, StreamMessage) and e.event == StreamEvents.ANSWER_END]
        assert ends and "echoed" in ends[-1].data["content"]

    def test_approval_pause_resume(self):
        """审批暂停 → PauseRequest(approval) → per-id resume 批准 → 执行 → 回答。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(risky_tool, requires_approval=True)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_tool", "args": {"text": "x"}, "id": "c2", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="done after approval", response_metadata=_usage()),
            ]
        )
        agent = _new_agent(reg, llm)
        events1 = list(agent.stream(messages=[{"role": "user", "content": "run"}], session_id="ga-3"))
        pauses = [e for e in events1 if isinstance(e, PauseRequest)]
        assert pauses and pauses[0].type == "approval", "应有审批暂停"
        assert EXECUTED == [], "审批前工具不得执行"
        assert not any(
            isinstance(e, StreamMessage) and e.event == StreamEvents.ANSWER_END for e in events1
        )

        # 恢复（per-id resume）
        resume = {pauses[0].id: {"approved": True}}
        events2 = list(agent.stream(messages=[{"role": "user", "content": "run"}], session_id="ga-3", resume=resume))
        ends = [e for e in events2 if isinstance(e, StreamMessage) and e.event == StreamEvents.ANSWER_END]
        assert ends and "done after approval" in ends[-1].data["content"]
        assert ("risky_tool", "x") in EXECUTED, "批准后工具应执行"

    def test_approval_deny(self):
        """审批拒绝：工具不执行，模型继续回答。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(risky_tool, requires_approval=True)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_tool", "args": {"text": "x"}, "id": "c3", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="skipped", response_metadata=_usage()),
            ]
        )
        agent = _new_agent(reg, llm)
        events1 = list(agent.stream(messages=[{"role": "user", "content": "run"}], session_id="ga-4"))
        pauses = [e for e in events1 if isinstance(e, PauseRequest)]
        assert pauses, "应有审批暂停"
        events2 = list(
            agent.stream(
                messages=[{"role": "user", "content": "run"}], session_id="ga-4", resume={pauses[0].id: {"approved": False}}
            )
        )
        assert EXECUTED == [], "拒绝后工具不得执行"
        ends = [e for e in events2 if isinstance(e, StreamMessage) and e.event == StreamEvents.ANSWER_END]
        assert ends and "skipped" in ends[-1].data["content"]

    def test_sqlite_checkpointer(self):
        """SqliteSaver 可注入：编译正常，thread_id 维度会话可查。"""
        import tempfile

        from langgraph.checkpoint.sqlite import SqliteSaver

        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(echo_tool)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "echo_tool", "args": {"text": "hi"}, "id": "c4", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="with sqlite", response_metadata=_usage()),
            ]
        )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        import sqlite3

        conn = sqlite3.connect(db_path, check_same_thread=False)
        saver = SqliteSaver(conn)
        agent = _new_agent(reg, llm, checkpointer=saver)
        events = list(agent.stream(messages=[{"role": "user", "content": "run"}], session_id="ga-sql"))
        ends = [e for e in events if isinstance(e, StreamMessage) and e.event == StreamEvents.ANSWER_END]
        assert ends and "with sqlite" in ends[-1].data["content"]
        conn.close()
        import os

        os.unlink(db_path)
