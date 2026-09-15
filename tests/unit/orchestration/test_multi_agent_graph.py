"""多 Agent 主编排图测试（纯 langgraph 重构，Phase 3）。

FakeChatLLM 离线打桩。验证 create_agent + Send 主编排图：
- decompose（LLM/启发式）→ Send 并行 → gather → finalize 完整流程
- 命令子任务审批 interrupt 冒泡 → 父图 resume → 子图恢复执行
- checkpointer 共享（父图 + worker 同一 saver）
- 无 ThreadPoolExecutor / InProcessA2AClient 参与
"""

import json
import re

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from GSagent.core.orchestration.multi import build_multi_agent_graph
from GSagent.core.orchestration.workers import (
    create_business_worker,
    create_command_worker,
)
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents, StreamMessage
from tests.helpers import FakeChatLLM

EXEC: list = []


@tool
def risky_tool(cmd: str) -> str:
    """需审批的命令工具"""
    EXEC.append(("risky_tool", cmd))
    return f"executed {cmd}"


def _usage(p=5, c=3):
    return {"token_usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}}


def _build_graph(*, with_approval=False, saver=None):
    """装配多 Agent 图：decompose LLM 直接产出 2 个子任务（business+command）。"""
    reg = ToolRegistry()
    reg.register(risky_tool, requires_approval=with_approval)

    # 拆解 LLM：返回 JSON 数组 [business, command]
    def _decompose_resp():
        return AIMessage(
            content=json.dumps(
                [
                    {"kind": "business", "text": "总结一下"},
                    {"kind": "command", "text": "rm -rf tmp"},
                ]
            ),
            response_metadata=_usage(),
        )

    orchestrator_llm = FakeChatLLM(responses=[_decompose_resp()])
    # finalize LLM：归并子任务结果
    finalizer_llm = FakeChatLLM(
        responses=[AIMessage(content="最终归纳结果", response_metadata=_usage())]
    )
    # business worker：纯文本回答
    business_llm = FakeChatLLM(
        responses=[AIMessage(content="业务结果", response_metadata=_usage())]
    )
    # command worker：调用需审批工具
    if with_approval:
        command_llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "risky_tool", "args": {"cmd": "rm -rf tmp"}, "id": "c1", "type": "tool_call"}],
                    response_metadata=_usage(),
                ),
                AIMessage(content="命令执行完成", response_metadata=_usage()),
            ]
        )
    else:
        command_llm = FakeChatLLM(
            responses=[AIMessage(content="命令结果", response_metadata=_usage())]
        )

    business_worker = create_business_worker(business_llm, checkpointer=saver)
    command_worker = create_command_worker(
        command_llm, tools_registry=reg, checkpointer=saver
    )
    return build_multi_agent_graph(
        orchestrator_llm=orchestrator_llm,
        finalizer_llm=finalizer_llm,
        business_worker=business_worker,
        command_worker=command_worker,
        checkpointer=saver,
    )


def _run_stream(graph, saver):
    cfg = {"configurable": {"thread_id": "ma-1"}}
    interrupts = {}
    done = None
    for chunk in graph.stream(
        {"messages": [HumanMessage(content="处理任务")]}, cfg, stream_mode="updates"
    ):
        if "__interrupt__" in chunk:
            for intr in chunk["__interrupt__"]:
                interrupts[intr.id] = intr
        else:
            for node, updates in chunk.items():
                for sm in (updates or {}).get("_stream_messages") or []:
                    msg = sm if isinstance(sm, StreamMessage) else StreamMessage(**sm)
                    if msg.event == StreamEvents.ANSWER_END:
                        done = msg
    return interrupts, done, cfg


class TestMultiAgentGraph:
    def test_full_flow(self):
        """decompose → Send 并行 → gather → finalize → ANSWER_END。"""
        EXEC.clear()
        saver = __import__("langgraph.checkpoint.memory", fromlist=["MemorySaver"]).MemorySaver()
        graph = _build_graph(saver=saver)
        interrupts, done, _ = _run_stream(graph, saver)
        assert interrupts == {}, "无审批场景不应中断"
        assert done is not None, "应有 ANSWER_END"
        assert "最终归纳结果" in done.data["content"]

    def test_approval_bubbles_to_parent(self):
        """命令子任务审批 interrupt 冒泡到父图 → 父图 resume → 子图恢复执行。"""
        EXEC.clear()
        saver = __import__("langgraph.checkpoint.memory", fromlist=["MemorySaver"]).MemorySaver()
        graph = _build_graph(with_approval=True, saver=saver)
        interrupts, done, cfg = _run_stream(graph, saver)
        assert interrupts, "应有审批中断冒泡到父图"
        assert EXEC == [], "审批前工具不得执行"
        assert done is None, "审批未决策前不应有最终答案"

        # 父图 resume（per-id）
        resume_map = {iid: {"approved": True} for iid in interrupts}
        final_done = None
        from langgraph.types import Command

        for chunk in graph.stream(Command(resume=resume_map), cfg, stream_mode="updates"):
            if "__interrupt__" in chunk:
                raise AssertionError(f"不应再中断: {chunk['__interrupt__']}")
            for node, updates in chunk.items():
                for sm in (updates or {}).get("_stream_messages") or []:
                    msg = sm if isinstance(sm, StreamMessage) else StreamMessage(**sm)
                    if msg.event == StreamEvents.ANSWER_END:
                        final_done = msg
        assert EXEC == [("risky_tool", "rm -rf tmp")], "批准后命令应执行"
        assert final_done is not None, "resume 后应有最终答案"
        assert "最终归纳结果" in final_done.data["content"]

    def test_approval_deny_skips(self):
        """审批拒绝：命令不执行，继续完成。"""
        EXEC.clear()
        saver = __import__("langgraph.checkpoint.memory", fromlist=["MemorySaver"]).MemorySaver()
        graph = _build_graph(with_approval=True, saver=saver)
        interrupts, _, cfg = _run_stream(graph, saver)
        assert interrupts
        from langgraph.types import Command

        for chunk in graph.stream(
            Command(resume={iid: {"approved": False} for iid in interrupts}), cfg, stream_mode="updates"
        ):
            if "__interrupt__" in chunk:
                raise AssertionError("不应再中断")
        assert EXEC == [], "拒绝后命令不得执行"

    def test_decompose_heuristic_fallback(self):
        """LLM 拆解失败 → 启发式切分命令。"""
        from GSagent.core.orchestration.multi import decompose_heuristic

        subs = decompose_heuristic("ls -la && echo hi")
        assert subs == [
            {"kind": "command", "text": "ls -la"},
            {"kind": "command", "text": "echo hi"},
        ]
