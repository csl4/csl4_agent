"""serve + GraphAgent 集成测试（纯 langgraph 重构，Phase 6）。

验证 create_app 装配 GraphAgent 后：
- 线程创建 → 发消息 → SSE 事件流回放 ANSWER_END
- 需审批工具在 serve（无审批界面）下自动拒绝
- 后台任务 worker 用 GraphAgent 执行（自动拒绝审批）
"""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from GSagent.core.agents.graph_agent import GraphAgent
from GSagent.core.runtime.server import create_app
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents
from tests.helpers import FakeChatLLM

EXEC: list = []


@tool
def echo_tool(text: str) -> str:
    """echo tool"""
    EXEC.append(("echo", text))
    return f"echo:{text}"


@tool
def risky_tool(cmd: str) -> str:
    """需审批工具"""
    EXEC.append(("risky", cmd))
    return f"exec {cmd}"


def _usage(p=5, c=3):
    return {"token_usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}}


def _test_client(agent):
    from fastapi.testclient import TestClient

    return TestClient(create_app(agent=agent, start_worker=False))


class TestServeThread:
    def test_thread_roundtrip_sse(self):
        """创建线程 → 发消息 → SSE 事件流包含 ANSWER_END 与答案。"""
        EXEC.clear()
        reg = ToolRegistry()
        reg.register(echo_tool)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "echo_tool", "args": {"text": "hi"}, "id": "c1", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="serve answer done", response_metadata=_usage()),
            ]
        )
        agent = GraphAgent(chat_model=llm, tools_registry=reg, enable_compaction=False)
        client = _test_client(agent)
        tid = client.post("/threads").json()["thread_id"]
        assert tid
        client.post(f"/threads/{tid}/messages", json={"content": "hello"})
        with client.stream("GET", f"/threads/{tid}/events") as resp:
            data = resp.read().decode("utf-8", errors="replace")
        assert "serve answer done" in data
        assert StreamEvents.ANSWER_END.value in data
        assert ("echo", "hi") in EXEC, "工具应执行"

    def test_approval_auto_denied_in_serve(self):
        """serve 无审批界面：需审批工具自动拒绝，不执行。"""
        EXEC.clear()
        reg = ToolRegistry()
        reg.register(risky_tool, requires_approval=True)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_tool", "args": {"cmd": "rm x"}, "id": "c2", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="serve denied", response_metadata=_usage()),
            ]
        )
        agent = GraphAgent(chat_model=llm, tools_registry=reg, enable_compaction=False)
        client = _test_client(agent)
        tid = client.post("/threads").json()["thread_id"]
        client.post(f"/threads/{tid}/messages", json={"content": "do risky"})
        with client.stream("GET", f"/threads/{tid}/events") as resp:
            data = resp.read().decode("utf-8", errors="replace")
        assert EXEC == [], "serve 自动拒绝后工具不得执行"
        assert "serve denied" in data
