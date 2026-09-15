"""Plan-and-Execute（纯 langgraph 重构）：规划器 + 结果模型。

plan 模式：把用户任务经 LLM 拆解为带依赖的子任务 DAG，经
``core/orchestration/plan.py`` 的 Plan 图按拓扑批次 Send 并行执行，
失败定位到具体子任务（FR-008，R-07）。执行器（旧 ThreadPoolExecutor）已删。
"""

from GSagent.core.plan.planner import (
    PLANNER_SYSTEM_PROMPT,
    PlanError,
    PlanRunResult,
    PlanTask,
    PlanTaskResult,
    compute_batches,
    merge_plan_results,
    parse_plan_json,
    plan_with_llm,
)

__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "PlanError",
    "PlanRunResult",
    "PlanTask",
    "PlanTaskResult",
    "compute_batches",
    "merge_plan_results",
    "parse_plan_json",
    "plan_with_llm",
]
