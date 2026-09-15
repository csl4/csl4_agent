"""Plan-and-Execute 执行器（002-enterprise-cli-upgrade US3 T027，FR-008/R-07）。

按 planner 产出的拓扑批次逐批执行：批内子任务并行（ThreadPoolExecutor），
整批完成后才进入下一批（依赖保证）。每个子任务复用 A2A SubAgent 执行骨架
（命令/JSON 描述符子任务经同一 ToolExecutor 执行 —— 与主 Agent 共享同一套
policy/audit），经 InProcessA2AClient（tenacity 自动重试）派发。

失败定位（SC-005）：
- 每个子任务独立记录结果（task_id + state），失败绝不被静默吞掉；
- 依赖失败的子任务按 make 语义标记 skipped（不派发不执行），由
  merge_plan_results 如实呈现具体失败的 task_id。

事件契约（contracts/cli.md）：run_stream 产出 StreamMessage——
  PLAN（data: tasks/batches）与 PLAN_TASK（data: task_id/description/status/result/batch），
CLI 只消费事件流（宪法 II）。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional, Set

from GSagent.core.a2a.client import A2AClientError, InProcessA2AClient
from GSagent.core.agents.base_agent import BaseAgent, task_state_text
from GSagent.core.agents.subagent import SubAgent
from GSagent.core.plan.planner import (
    PlanRunResult,
    PlanTask,
    PlanTaskResult,
    compute_batches,
    merge_plan_results,
)
from GSagent.core.tools.registry import ToolRegistry
from GSagent.utils.stream import StreamEvents, StreamMessage

logger = logging.getLogger(__name__)


class PlanExecutor:
    """按依赖批次并行执行子任务 DAG（R-07）。"""

    def __init__(
        self,
        tools_registry: ToolRegistry,
        max_subagents: int = 4,
        worker_factory: Optional[Callable[[str], BaseAgent]] = None,
        parent: Optional[BaseAgent] = None,
        name: str = "plan",
    ) -> None:
        self.tools_registry = tools_registry
        self.max_subagents = max_subagents
        self.parent = parent
        self.name = name
        # worker_factory(agent_id) -> BaseAgent；默认创建 SubAgent（命令/描述符执行）。
        self.worker_factory = worker_factory or self._default_worker
        self.last_run: Optional[PlanRunResult] = None

    def _default_worker(self, agent_id: str) -> BaseAgent:
        return SubAgent(
            agent_id=agent_id,
            tools_registry=self.tools_registry,
            name=f"plan-{agent_id}",
            parent=self.parent,
        )

    def run_stream(
        self,
        tasks: List[PlanTask],
        parent_id: str = "plan",
    ) -> Generator[StreamMessage, None, PlanRunResult]:
        """执行计划并逐条产出事件（PLAN + PLAN_TASK）。

        生成器终止时（StopIteration.value）返回 PlanRunResult；同时写入
        self.last_run 供 CLI 在事件循环结束后读取归并结果（宪法 II：
        CLI 只消费事件流，不直接调用执行器细节）。
        """
        batches = compute_batches(tasks)
        by_id = {t.id: t for t in tasks}

        yield StreamMessage(
            event=StreamEvents.PLAN,
            data={
                "tasks": [t.to_dict() for t in tasks],
                "batches": batches,
            },
        )

        results: Dict[str, PlanTaskResult] = {}
        failed: Set[str] = set()

        for batch_index, batch in enumerate(batches):
            runnable: List[str] = []
            for tid in batch:
                # make 语义：依赖失败的子任务标记 skipped，不派发不执行。
                if any(dep in failed for dep in by_id[tid].depends_on):
                    results[tid] = PlanTaskResult(
                        tid,
                        by_id[tid].description,
                        "skipped",
                        "依赖的子任务失败，已跳过。",
                        batch_index,
                    )
                    yield StreamMessage(
                        event=StreamEvents.PLAN_TASK, data=results[tid].to_dict()
                    )
                else:
                    runnable.append(tid)

            max_workers = max(1, min(self.max_subagents, len(runnable)))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(self._run_one, tid, by_id, parent_id, batch_index)
                    for tid in runnable
                ]
                for fut in as_completed(futures):
                    res = fut.result()
                    results[res.task_id] = res
                    if res.state == "failed":
                        failed.add(res.task_id)
                    yield StreamMessage(
                        event=StreamEvents.PLAN_TASK, data=res.to_dict()
                    )

        self.last_run = PlanRunResult(tasks=tasks, batches=batches, results=results)
        return self.last_run

    def _run_one(
        self,
        tid: str,
        by_id: Dict[str, PlanTask],
        parent_id: str,
        batch_index: int,
    ) -> PlanTaskResult:
        """单个子任务：经 InProcessA2AClient 派发给 worker，异常 → failed。"""
        task = by_id[tid]
        worker = self.worker_factory(f"{parent_id}-{tid}")
        client = InProcessA2AClient(worker)
        try:
            sub_task = client.send_task(task.description, task_id=f"{parent_id}-{tid}")
            return PlanTaskResult(
                tid,
                task.description,
                "completed",
                task_state_text(sub_task),
                batch_index,
            )
        except A2AClientError as exc:
            logger.warning("计划子任务 %s (%s) 失败: %s", tid, task.description, exc)
            return PlanTaskResult(
                tid,
                task.description,
                "failed",
                str(exc),
                batch_index,
            )

    def run(self, tasks: List[PlanTask], parent_id: str = "plan") -> PlanRunResult:
        """执行计划并返回整体结果（同步入口，事件流见 run_stream）。"""
        gen = self.run_stream(tasks, parent_id=parent_id)
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            return stop.value
        # 不可达（生成器必有终止返回值）
        assert self.last_run is not None
        return self.last_run


__all__ = [
    "PlanExecutor",
    "PlanRunResult",
    "PlanTaskResult",
    "merge_plan_results",
]
