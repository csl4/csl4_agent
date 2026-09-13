"""lc_tools 集成测试（T021 部分）：@tool 迁移的工具经 registry + 编排工作。

验证（SC-002）：filesystem/memory 工具注册进 ToolRegistry 后，FakeChatLLM 编排
能正确分发执行（AIMessage.tool_calls → @tool → ToolMessage），守卫不误拦合法路径。
"""

from langchain_core.messages import AIMessage

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.policy.path_guard import PathGuard
from GSagent.core.tools.registry import ToolRegistry
from GSagent.plugins.toolsets.filesystem.lc_tools import create_filesystem_tools
from GSagent.plugins.toolsets.memory.lc_tools import create_memory_tools
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


class TestLcToolsFlow:
    def test_filesystem_read_file_via_orchestration(self, tmp_path):
        """read_file 经编排执行，结果进入 ToolMessage 与事件快照。"""
        (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
        reg = ToolRegistry().configure(
            path_guard=PathGuard(workspace_root=str(tmp_path))
        )
        for t in create_filesystem_tools({"root_dir": str(tmp_path)}):
            reg.register(t)

        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "a.txt"},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                    response_metadata=_usage(5, 3),
                ),
                AIMessage(content="file read done", response_metadata=_usage(10, 4)),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False
        )
        events = list(agent.call_stream(messages=[{"role": "user", "content": "read"}]))
        end = [e for e in events if e.event == StreamEvents.ANSWER_END]
        assert end, "应有 ANSWER_END"
        # 消息快照含 read_file 工具结果（hello world）
        tool_msgs = [m for m in end[-1].data["messages"] if m.get("role") == "tool"]
        assert tool_msgs and "hello world" in tool_msgs[0]["content"]

    def test_memory_remember_via_orchestration(self, tmp_path):
        """remember 经编排执行（无守卫，走 MemoryStore）。"""
        reg = ToolRegistry()
        for t in create_memory_tools(
            {"db_path": str(tmp_path / "mem.db"), "scope": str(tmp_path)}
        ):
            reg.register(t)

        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "remember",
                            "args": {"content": "user likes python", "kind": "preference"},
                            "id": "c2",
                            "type": "tool_call",
                        }
                    ],
                    response_metadata=_usage(5, 3),
                ),
                AIMessage(content="stored", response_metadata=_usage(10, 4)),
            ]
        )
        agent = ToolCallingLLM(
            chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False
        )
        events = list(agent.call_stream(messages=[{"role": "user", "content": "remember"}]))
        end = [e for e in events if e.event == StreamEvents.ANSWER_END]
        assert end and "stored" in end[-1].data["content"]
