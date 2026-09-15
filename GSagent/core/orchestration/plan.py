"""Plan-and-Execute 图（纯 langgraph 重构，Phase 4）。

替换旧 ``plan/executor.py`` 的 ThreadPoolExecutor 拓扑批次执行：

``START → plan → dispatch(Send) → run_one(create_agent 子图) → collect → (批次循环) → finalize → END``

- ``plan``：LLM 规划子任务 DAG（复用 ``plan_with_llm``/``compute_batches``）。
- ``dispatch`` 节点：返回 ``[Send]`` 原生并行执行当前批次（make 语义：依赖失败
  的子任务 Send 到 ``skip_task`` 标记 skipped）。
- ``run_one``：create_agent 命令 worker 官方子图（工具审批 interrupt 自动冒泡）。
- ``collect``：按 HumanMessage(description)→AIMessage 配对提取本批结果；还有批次
  则 ``Command(goto="dispatch", update={batch_index:+1})`` 推进，否则进 finalize。
- ``finalize``：归并（复用 ``merge_plan_results``）→ ANSWER_END。

事件：PLAN / PLAN_TASK / ANSWER_DELTA / ANSWER_END。
"""

import logging
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send

from GSagent.core.plan.planner import (
    PlanError,
    PlanRunResult,
    PlanTask,
    PlanTaskResult,
    compute_batches,
    merge_plan_results,
    plan_with_llm,
)
from GSagent.utils.stream import StreamEvents, stream_custom

logger = logging.getLogger(__name__)


def _dict_merge(left: Optional[Dict[str, Any]], right: Dict[str, Any]) -> Dict[str, Any]:
    """Merge reducer：results 通道按 task_id 增量合并。"""
    return {**(left or {}), **(right or {})}


class PlanGraphState(TypedDict, total=False):
    """Plan-and-Execute 图状态。"""

    messages: Annotated[list[BaseMessage], add_messages]  # run_one 子图结果
    plan_tasks: List[Dict[str, Any]]  # [{id, description, depends_on}]
    batches: List[List[str]]
    batch_index: int
    results: Annotated[Dict[str, Any], _dict_merge]  # {tid: PlanTaskResult dict}
    failed: List[str]
    request_context: Optional[Dict[str, Any]]


# ---- 节点 ----
def plan_node_factory(planner_llm: Any) -> Any:
    """plan 节点：LLM 规划 DAG → batches；失败产 PLAN 错误事件。"""

    def _plan(state: Dict[str, Any]) -> Dict[str, Any]:
        writer = get_stream_writer()
        messages: list[BaseMessage] = list(state.get("messages") or [])
        user_text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                user_text = str(m.content or "")
                break
        try:
            tasks = plan_with_llm(planner_llm, user_text)
        except PlanError as exc:
            stream_custom(
                writer, StreamEvents.PLAN, {"error": str(exc), "tasks": [], "batches": []}
            )
            stream_custom(writer, StreamEvents.ERROR, {"error": f"计划失败: {exc}"})
            return {}
        batches = compute_batches(tasks)
        stream_custom(
            writer,
            StreamEvents.PLAN,
            {"tasks": [t.to_dict() for t in tasks], "batches": batches},
        )
        return {
            "plan_tasks": [t.to_dict() for t in tasks],
            "batches": batches,
            "batch_index": 0,
            "results": {},
            "failed": [],
        }

    return _plan


def dispatch(state: Dict[str, Any]) -> List[Send]:
    """dispatch 条件边：Send 当前批次（依赖失败 → skip_task）。"""
    batch_index = int(state.get("batch_index", 0))
    batches: List[List[str]] = state.get("batches") or []
    if batch_index >= len(batches):
        return []
    batch = batches[batch_index]
    by_id = {t["id"]: t for t in state.get("plan_tasks") or []}
    failed = set(state.get("failed") or [])
    sends: List[Send] = []
    for tid in batch:
        task = by_id.get(tid)
        deps = (task or {}).get("depends_on", []) if task else []
        if any(d in failed for d in deps):
            sends.append(Send("skip_task", {"task_id": tid, "batch_index": batch_index}))
        else:
            sends.append(
                Send(
                    "run_one",
                    {
                        "messages": [HumanMessage(content=str((task or {}).get("description", "")))],
                        "task_id": tid,
                        "batch_index": batch_index,
                    },
                )
            )
    return sends


def skip_task(state: Dict[str, Any]) -> Dict[str, Any]:
    """skip_task 节点：依赖失败的子任务标记 skipped。"""
    tid = state.get("task_id", "")
    by_id = {t["id"]: t for t in state.get("plan_tasks") or []}
    task = by_id.get(tid, {})
    result = {
        "task_id": tid,
        "description": task.get("description", ""),
        "status": "skipped",
        "result": "依赖的子任务失败，已跳过。",
        "batch": int(state.get("batch_index", 0)),
    }
    writer = get_stream_writer()
    stream_custom(writer, StreamEvents.PLAN_TASK, result)
    return {"results": {tid: result}}


def collect(state: Dict[str, Any]) -> Dict[str, Any]:
    """collect 节点：汇总结果 + 推进批次索引（route_after_collect 决定继续/收尾）。"""
    batch_index = int(state.get("batch_index", 0))
    results = dict(state.get("results") or {})
    by_id = {t["id"]: t for t in state.get("plan_tasks") or []}

    # 从 messages 配对 HumanMessage(description) → AIMessage 结果
    desc_to_tid = {t.get("description", ""): tid for tid, t in by_id.items()}
    current_desc = ""
    for m in state.get("messages") or []:
        if m.type == "human" and str(m.content or "") in desc_to_tid:
            current_desc = str(m.content or "")
        elif m.type == "ai" and current_desc:
            tid = desc_to_tid.get(current_desc)
            if tid and tid not in results:
                results[tid] = {
                    "task_id": tid,
                    "description": current_desc,
                    "status": "completed",
                    "result": str(m.content or ""),
                    "batch": batch_index,
                }
            current_desc = ""

    # failed 集合（本批）
    failed = list(state.get("failed") or [])
    batches: List[List[str]] = state.get("batches") or []
    if batch_index < len(batches):
        for tid in batches[batch_index]:
            res = results.get(tid)
            if res and res.get("status") == "failed" and tid not in failed:
                failed.append(tid)

    return {
        "results": results,
        "failed": failed,
        "batch_index": batch_index + 1,
    }


def route_after_collect(state: Dict[str, Any]) -> str:
    """collect 之后：还有批次 → dispatch；否则 → finalize。"""
    next_idx = int(state.get("batch_index", 0))
    if next_idx < len(state.get("batches") or []):
        return "dispatch"
    return "finalize"


def finalize_node_factory() -> Any:
    """finalize 节点：归并结果 → ANSWER_END。"""

    def _finalize(state: Dict[str, Any]) -> Dict[str, Any]:
        plan_tasks = [PlanTask(**t) for t in state.get("plan_tasks") or []]
        batches: List[List[str]] = state.get("batches") or []
        results = {
            tid: PlanTaskResult(
                task_id=res.get("task_id", tid),
                description=res.get("description", ""),
                state=res.get("status", "failed"),
                result=res.get("result", ""),
                batch=int(res.get("batch", 0)),
            )
            for tid, res in (state.get("results") or {}).items()
        }
        run = PlanRunResult(tasks=plan_tasks, batches=batches, results=results)
        merged = merge_plan_results(run)
        writer = get_stream_writer()
        stream_custom(writer, StreamEvents.ANSWER_DELTA, {"content": merged})
        stream_custom(writer, StreamEvents.ANSWER_END, {"content": merged, "messages": []})
        return {}

    return _finalize


def build_plan_graph(
    *,
    planner_llm: Any,
    command_worker: Any,
    checkpointer: Any = None,
    store: Any = None,
) -> Any:
    """组装并编译 Plan-and-Execute 图。

    参数:
        planner_llm: 规划 LLM（plan 节点）。
        command_worker: create_agent 命令 worker（run_one 官方子图）。
        checkpointer: 与 worker 共享的 checkpointer。
        store: langgraph store。
    """
    builder = StateGraph(PlanGraphState)
    builder.add_node("plan", plan_node_factory(planner_llm))
    builder.add_node("dispatch_entry", lambda _state: {})  # 空节点，供条件边 goto 循环
    builder.add_node("run_one", command_worker)  # 官方子图节点（审批自动冒泡）
    builder.add_node("skip_task", skip_task)
    builder.add_node("collect", collect)
    builder.add_node("finalize", finalize_node_factory())

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "dispatch_entry")
    builder.add_conditional_edges("dispatch_entry", dispatch, ["run_one", "skip_task"])
    builder.add_edge("run_one", "collect")
    builder.add_edge("skip_task", "collect")
    builder.add_conditional_edges(
        "collect",
        route_after_collect,
        {"dispatch": "dispatch_entry", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer, store=store)


__all__ = [
    "PlanGraphState",
    "build_plan_graph",
    "collect",
    "dispatch",
]
