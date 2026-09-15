"""CLI 图消费集成测试（纯 langgraph 重构，Phase 5）。

验证 main.py 的 _run_turn 消费 GraphAgent.stream（StreamMessage + PauseRequest）：
- 单 Agent 无审批：直接完成
- 单 Agent 审批 + 非交互终端：自动拒绝 → 完成
- 多 Agent 主编排图：decompose → Send 并行 → finalize 完成
- Plan 图：规划 → 批次执行 → 完成
"""

import json

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from GSagent.core.agents.graph_agent import GraphAgent
from GSagent.core.orchestration.multi import build_multi_agent_graph
from GSagent.core.orchestration.plan import build_plan_graph
from GSagent.core.orchestration.workers import (
    create_business_worker,
    create_command_worker,
)
from GSagent.core.tools.registry import ToolRegistry
from GSagent.main import _run_turn
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


def _msgs(text="hello"):
    return [{"role": "user", "content": text}]


class TestSingleAgentCli:
    def test_turn_completes(self):
        llm = FakeChatLLM(responses=[AIMessage(content="hi there", response_metadata=_usage())])
        agent = GraphAgent(chat_model=llm, enable_compaction=False)
        final, hist = _run_turn(agent, _msgs(), can_prompt=False, session_id="cli-s1")
        assert final is not None
        assert "hi there" in final["content"]
        assert final.get("approved") == {"approved": 0, "denied": 0, "auto_denied": 0}

    def test_approval_auto_denied_non_tty(self):
        """非交互终端（can_prompt=False）：审批自动拒绝，工具不执行。"""
        EXEC.clear()
        reg = ToolRegistry()
        reg.register(risky_tool, requires_approval=True)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_tool", "args": {"cmd": "rm x"}, "id": "c1", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="skipped by user", response_metadata=_usage()),
            ]
        )
        agent = GraphAgent(chat_model=llm, tools_registry=reg, enable_compaction=False)
        final, _ = _run_turn(agent, _msgs("do risky"), can_prompt=False, session_id="cli-s2")
        assert final is not None and "skipped" in final["content"]
        assert EXEC == [], "审批自动拒绝后工具不得执行"
        assert final["approved"]["auto_denied"] == 1

    def test_approval_approved(self, monkeypatch):
        """交互终端审批通过：工具执行并完成。"""
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
                AIMessage(content="approved done", response_metadata=_usage()),
            ]
        )
        agent = GraphAgent(chat_model=llm, tools_registry=reg, enable_compaction=False)
        monkeypatch.setattr("GSagent.main.typer.confirm", lambda *a, **k: True)
        final, _ = _run_turn(agent, _msgs("do risky"), can_prompt=True, session_id="cli-s3")
        assert EXEC == [("risky", "rm x")], "批准后工具应执行"
        assert final is not None and "approved done" in final["content"]
        assert final["approved"]["approved"] == 1


class TestMultiAgentCli:
    def _build_multi(self, saver=None):
        reg = ToolRegistry()
        reg.register(echo_tool)
        orchestrator_llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content=json.dumps(
                        [{"kind": "business", "text": "总结"}, {"kind": "business", "text": "分析"}]
                    ),
                    response_metadata=_usage(),
                )
            ]
        )
        finalizer_llm = FakeChatLLM(responses=[AIMessage(content="多Agent 归纳结果", response_metadata=_usage())])
        business_llm = FakeChatLLM(responses=[AIMessage(content="业务结果", response_metadata=_usage())])
        command_llm = FakeChatLLM(responses=[AIMessage(content="命令结果", response_metadata=_usage())])
        business_worker = create_business_worker(business_llm, checkpointer=saver)
        command_worker = create_command_worker(command_llm, tools_registry=reg, checkpointer=saver)
        graph = build_multi_agent_graph(
            orchestrator_llm=orchestrator_llm,
            finalizer_llm=finalizer_llm,
            business_worker=business_worker,
            command_worker=command_worker,
            checkpointer=saver,
        )
        return GraphAgent(graph=graph, tools_registry=reg, enable_compaction=False)

    def test_multi_agent_turn_completes(self):
        saver = __import__("langgraph.checkpoint.memory", fromlist=["MemorySaver"]).MemorySaver()
        agent = self._build_multi(saver)
        final, _ = _run_turn(agent, _msgs("处理任务"), can_prompt=False, session_id="cli-m1")
        assert final is not None
        assert "多Agent 归纳结果" in final["content"]


class TestPlanCli:
    def test_plan_turn_completes(self):
        saver = __import__("langgraph.checkpoint.memory", fromlist=["MemorySaver"]).MemorySaver()
        reg = ToolRegistry()
        reg.register(echo_tool)
        planner_llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content=json.dumps(
                        [{"id": "1", "description": "子任务一", "depends_on": []}]
                    ),
                    response_metadata=_usage(),
                )
            ]
        )
        command_llm = FakeChatLLM(responses=[AIMessage(content="plan 子任务完成", response_metadata=_usage())])
        command_worker = create_command_worker(command_llm, tools_registry=reg, checkpointer=saver)
        graph = build_plan_graph(
            planner_llm=planner_llm, command_worker=command_worker, checkpointer=saver
        )
        agent = GraphAgent(graph=graph, tools_registry=reg, enable_compaction=False)
        final, _ = _run_turn(agent, _msgs("规划执行"), can_prompt=False, session_id="cli-p1")
        assert final is not None
        assert "计划执行完成" in final["content"]
