"""Plan-and-Execute（US3）：规划器 + 执行器。

plan 模式：把用户任务经 LLM 拆解为带依赖的子任务 DAG，按拓扑批次并行执行，
失败定位到具体子任务（FR-008，R-07）。
"""

from GSagent.core.plan.executor import (
    PlanExecutor,
    PlanRunResult,
    PlanTaskResult,
    merge_plan_results,
)
from GSagent.core.plan.planner import (
    PLANNER_SYSTEM_PROMPT,
    PlanError,
    PlanTask,
    compute_batches,
    parse_plan_json,
    plan_with_llm,
)

__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "PlanError",
    "PlanExecutor",
    "PlanRunResult",
    "PlanTask",
    "PlanTaskResult",
    "compute_batches",
    "merge_plan_results",
    "parse_plan_json",
    "plan_with_llm",
]
