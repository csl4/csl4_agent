"""审批下沉包装器测试（纯 langgraph 重构，Phase 1）。

FakeChatLLM 离线打桩 + create_agent（运行在 LangGraph 之上）。验证：
- 规则级审批（invoke 前 registry.approval_requirement）触发 __interrupt__（approval）
- 动态信号审批（工具返回 __approval_required__）触发 __interrupt__ 且批准后重新执行
- per-interrupt-id 恢复：批准执行 / 拒绝返回拒绝文本不执行
- 前端暂停（__frontend_pause__）触发 __interrupt__（frontend）
- 守卫（PathGuard）拦截仍生效（包装器叠加在已守卫工具上）
"""

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, Interrupt
from langchain.agents import create_agent

from GSagent.core.policy.path_guard import PathGuard
from GSagent.core.tools.approval import wrap_all_with_approval
from GSagent.core.tools.registry import ToolRegistry
from tests.helpers import FakeChatLLM

EXECUTED: list = []


@tool
def risky_write(path: str, content: str) -> str:
    """需审批（静态规则）的写工具。"""
    EXECUTED.append(("risky_write", path, content))
    return f"wrote {path}"


@tool
def dynamic_tool(cmd: str) -> str:
    """动态审批：审批判定前置到 approval_check（invoke 前），本函数仅执行。"""
    EXECUTED.append(("dynamic_tool", cmd))
    return f"ran {cmd}"


def _dynamic_check(args: dict):
    """模拟 validate_command 前置判定：cmd 含 'format' 需审批。"""
    cmd = (args or {}).get("cmd", "")
    if "format" in cmd:
        return {"reason": "format flag", "prefixes_to_save": None}
    return None


@tool
def frontend_tool() -> dict:
    """前端暂停工具。"""
    return {"__frontend_pause__": True}


@tool
def plain_tool(text: str) -> str:
    """无需审批的工具。"""
    EXECUTED.append(("plain_tool", text))
    return f"plain:{text}"


def _usage(p=5, c=3):
    return {"token_usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}}


def _collect_interrupts(graph, initial_input, config):
    """跑图直到中断或结束，返回 (interrupts: list[Interrupt], 是否结束)。"""
    interrupts: list[Interrupt] = []
    finished = False
    for chunk in graph.stream(initial_input, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            interrupts.extend(chunk["__interrupt__"])
    return interrupts


class TestApprovalWrapper:
    def test_rule_approval_interrupt_and_resume(self):
        """规则级审批：create_agent 内触发 __interrupt__；per-id resume 批准后执行。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(risky_write, requires_approval=True)
        tools = wrap_all_with_approval(reg.get_all_tools(), registry=reg)
        fake = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_write", "args": {"path": "a", "content": "x"}, "id": "c1", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="done writing", response_metadata=_usage()),
            ]
        )
        graph = create_agent(fake, tools=tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "aw-1"}}

        interrupts = _collect_interrupts(graph, {"messages": [HumanMessage(content="write a")]}, config)
        assert len(interrupts) == 1
        assert interrupts[0].value["type"] == "approval"
        assert interrupts[0].value["tool_name"] == "risky_write"
        assert EXECUTED == [], "审批前工具不得执行"

        # per-id resume：批准
        resume = Command(resume={interrupts[0].id: {"approved": True}})
        for chunk in graph.stream(resume, config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                raise AssertionError(f"不应再次中断: {chunk['__interrupt__']}")
        assert ("risky_write", "a", "x") in EXECUTED, "批准后工具应执行"

    def test_approval_deny_returns_denied(self):
        """审批拒绝：工具不执行，返回拒绝文本，模型继续回答。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(risky_write, requires_approval=True)
        tools = wrap_all_with_approval(reg.get_all_tools(), registry=reg)
        fake = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_write", "args": {"path": "a", "content": "x"}, "id": "c1", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="user denied", response_metadata=_usage()),
            ]
        )
        graph = create_agent(fake, tools=tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "aw-2"}}

        interrupts = _collect_interrupts(graph, {"messages": [HumanMessage(content="write a")]}, config)
        assert len(interrupts) == 1
        resume = Command(resume={interrupts[0].id: {"approved": False}})
        final = graph.invoke(resume, config)
        assert EXECUTED == [], "拒绝后工具不得执行"
        # ToolMessage 内容为拒绝文本
        tool_msgs = [m for m in final["messages"] if m.type == "tool"]
        assert tool_msgs and "denied" in tool_msgs[-1].content.lower()

    def test_dynamic_signal_approval(self):
        """动态审批（approval_check 前置）：invoke 前 interrupt → 批准后执行一次、无重复。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(dynamic_tool, approval_check=_dynamic_check)
        tools = wrap_all_with_approval(reg.get_all_tools(), registry=reg)
        fake = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "dynamic_tool", "args": {"cmd": "ls --format x"}, "id": "c2", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="ran with approval", response_metadata=_usage()),
            ]
        )
        graph = create_agent(fake, tools=tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "aw-3"}}

        interrupts = _collect_interrupts(graph, {"messages": [HumanMessage(content="run ls")]}, config)
        assert len(interrupts) == 1
        assert interrupts[0].value["type"] == "approval"
        assert interrupts[0].value["reason"] == "format flag"
        # 审批判定前置：interrupt 前工具不执行
        assert EXECUTED == [], "审批前工具不得执行（invoke 前判定）"

        resume = Command(resume={interrupts[0].id: {"approved": True}})
        for chunk in graph.stream(resume, config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                raise AssertionError(f"不应再次中断: {chunk['__interrupt__']}")
        assert EXECUTED.count(("dynamic_tool", "ls --format x")) == 1, "批准后应执行一次、无重放重复"

    def test_frontend_pause(self):
        """前端暂停：__frontend_pause__ 信号 → __interrupt__(frontend) → resume 回填。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(frontend_tool)
        tools = wrap_all_with_approval(reg.get_all_tools(), registry=reg)
        fake = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "frontend_tool", "args": {}, "id": "c3", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="after frontend", response_metadata=_usage()),
            ]
        )
        graph = create_agent(fake, tools=tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "aw-4"}}

        interrupts = _collect_interrupts(graph, {"messages": [HumanMessage(content="go frontend")]}, config)
        assert len(interrupts) == 1
        assert interrupts[0].value["type"] == "frontend"

        resume = Command(resume={interrupts[0].id: {"frontend_tool_results": {"c3": {"ok": True}}}})
        final = graph.invoke(resume, config)
        assert "after frontend" in final["messages"][-1].content

    def test_no_approval_tool_runs_directly(self):
        """无需审批的工具直接执行，无中断。"""
        EXECUTED.clear()
        reg = ToolRegistry()
        reg.register(plain_tool)
        tools = wrap_all_with_approval(reg.get_all_tools(), registry=reg)
        fake = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "plain_tool", "args": {"text": "hi"}, "id": "c4", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="plain done", response_metadata=_usage()),
            ]
        )
        graph = create_agent(fake, tools=tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "aw-5"}}

        final = graph.invoke({"messages": [HumanMessage(content="run plain")]}, config)
        assert ("plain_tool", "hi") in EXECUTED
        assert "plain done" in final["messages"][-1].content

    def test_guard_still_blocks_with_approval_wrapper(self):
        """守卫层（PathGuard）拦截仍生效（包装器叠加在已守卫工具上）。"""
        EXECUTED.clear()
        reg = ToolRegistry().configure(path_guard=PathGuard(workspace_root="C:/safe"))

        @tool
        def read_file(path: str) -> str:
            """read a file（filesystem 类 → PathGuard 校验 path 参数）"""
            EXECUTED.append(("read_file", path))
            return f"content of {path}"

        reg.register(read_file)
        tools = wrap_all_with_approval(reg.get_all_tools(), registry=reg)
        fake = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "read_file", "args": {"path": "C:/evil"}, "id": "c5", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="blocked", response_metadata=_usage()),
            ]
        )
        graph = create_agent(fake, tools=tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "aw-6"}}

        final = graph.invoke({"messages": [HumanMessage(content="read evil")]}, config)
        assert EXECUTED == [], "守卫拦截后工具不得执行"
        tool_msgs = [m for m in final["messages"] if m.type == "tool"]
        assert tool_msgs and "blocked" in tool_msgs[-1].content.lower()
