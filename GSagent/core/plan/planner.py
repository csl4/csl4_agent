"""Plan-and-Execute 规划器（002-enterprise-cli-upgrade US3 T026，FR-008/R-07）。

把用户任务经 LLM 拆解为带依赖关系的子任务 DAG（[{id, description, depends_on}]），
再计算拓扑批次：每批内的子任务相互独立（可并行），批次之间按依赖先后执行。

校验（compute_batches 内）：id 唯一、依赖引用存在、依赖无环（含自依赖）。
容错（parse_plan_json 内）：允许 LLM 输出前后带杂散文本 / markdown 代码块；
非 dict 项、缺 description 的项跳过。

对齐 A2A 编排的 JSON 数组提取模式（Orchestrator._plan_with_llm，
orchestrator.py:163-180）：find("[") / rfind("]") + json.loads。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 规划器系统提示词：要求 LLM 输出子任务 DAG（无多余文字）。
PLANNER_SYSTEM_PROMPT = (
    "你是任务规划 Agent。请把用户任务拆解为若干有依赖关系的子任务，"
    "并给出执行顺序所需的依赖关系。只输出一个 JSON 数组，每个元素形如 "
    '{"id": "1", "description": "子任务描述", "depends_on": ["0"]}。'
    "要求：id 为唯一字符串；description 自包含、可直接交给子 Agent 执行；"
    "depends_on 列出本子任务依赖的子任务 id，无依赖则为空数组 []。"
    "不要输出任何其他文字。"
)


class PlanError(ValueError):
    """规划失败：LLM 输出不可解析 / 空规划 / id 重复 / 依赖缺失 / 依赖成环。"""


@dataclass
class PlanTask:
    """一个子任务（DAG 节点）。"""

    id: str
    description: str
    depends_on: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "depends_on": list(self.depends_on),
        }


def parse_plan_json(content: str) -> List[PlanTask]:
    """从 LLM 文本提取并解析子任务数组 [{id, description, depends_on}]。

    容错：数组前后可有杂散文本 / markdown 代码块（find "[" / rfind "]"）。
    非 dict 项、缺 id 或 description 的项跳过。
    抛 PlanError：找不到数组、JSON 非法、不是数组。
    """
    text = (content or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise PlanError("规划输出不包含 JSON 数组。")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise PlanError(f"规划 JSON 无法解析: {exc}") from exc
    if not isinstance(data, list):
        raise PlanError("规划 JSON 不是数组。")
    tasks: List[PlanTask] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tid, desc = item.get("id"), item.get("description")
        if tid is None or not desc:
            continue
        deps = item.get("depends_on")
        tasks.append(
            PlanTask(
                id=str(tid),
                description=str(desc),
                depends_on=[str(d) for d in deps] if isinstance(deps, list) else [],
            )
        )
    return tasks


def compute_batches(tasks: List[PlanTask]) -> List[List[str]]:
    """按依赖把子任务划分为拓扑批次（Kahn 分层）。

    返回 `List[List[str]]`：外层按依赖先后（批次 0 最先），每批内是相互
    独立的子任务 id 列表（可并行），顺序与输入保持稳定。
    抛 PlanError：id 重复 / 依赖引用不存在 / 依赖成环（含自依赖）。
    """
    if not tasks:
        return []
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise PlanError("子任务 id 重复。")
    by_id = {t.id: t for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in by_id:
                raise PlanError(f"子任务 {t.id} 依赖不存在的子任务 {dep}。")

    batches: List[List[str]] = []
    remaining = set(ids)
    while remaining:
        ready = [
            tid
            for tid in ids
            if tid in remaining and not (set(by_id[tid].depends_on) & remaining)
        ]
        if not ready:
            raise PlanError("子任务依赖成环，无法按拓扑序执行。")
        batches.append(ready)
        remaining.difference_update(ready)
    return batches


def _planner_text(llm: Any, messages: List[Dict[str, Any]]) -> str:
    """兼容 BaseChatModel（invoke）与旧 LLM（completion）取规划文本。"""
    invoke = getattr(llm, "invoke", None)
    if invoke is not None:
        from GSagent.core.llm_adapter import dict_to_messages

        response = invoke(dict_to_messages(messages))
        return (getattr(response, "content", "") or "").strip()
    response = llm.completion(messages)
    return (response.content or "").strip()


def plan_with_llm(
    llm: Any,
    user_input: str,
    system_prompt: Optional[str] = None,
) -> List[PlanTask]:
    """经 LLM 规划用户任务为子任务 DAG。

    调用方传入 langchain ``BaseChatModel``（invoke）或实现了
    ``completion(messages) -> ModelResponse`` 的旧 LLM（鸭子类型，与
    Orchestrator._plan_with_llm 一致）。
    抛 PlanError：LLM 调用失败 / 输出不可解析 / 规划为空。
    """
    try:
        content = _planner_text(
            llm,
            [
                {"role": "system", "content": system_prompt or PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_input or "(空任务)"},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - 规划失败收敛为 PlanError
        raise PlanError(f"规划 LLM 调用失败: {exc}") from exc
    tasks = parse_plan_json(content)
    if not tasks:
        raise PlanError("规划为空：未拆解出任何子任务。")
    return tasks


# ---- 执行结果模型（纯 langgraph 重构 Phase 4：从 executor.py 上移，供 Plan 图复用）----


@dataclass
class PlanTaskResult:
    """单个子任务的执行结果（失败定位的最小单位）。"""

    task_id: str
    description: str
    state: str  # completed / failed / skipped
    result: str
    batch: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "status": self.state,
            "result": self.result,
            "batch": self.batch,
        }


@dataclass
class PlanRunResult:
    """一次计划执行的整体结果。"""

    tasks: List[PlanTask]
    batches: List[List[str]]
    results: Dict[str, PlanTaskResult]

    def failed_tasks(self) -> List[PlanTaskResult]:
        """未成功（failed/skipped）的子任务，按 task_id 输入序。"""
        order = [t.id for t in self.tasks]
        return [
            self.results[i]
            for i in order
            if self.results.get(i) and self.results[i].state != "completed"
        ]


def merge_plan_results(run: PlanRunResult) -> str:
    """把计划执行结果归并为结构化回复（含批次明细 + 失败定位）。"""
    lines = [
        f"计划执行完成：共 {len(run.tasks)} 个子任务，{len(run.batches)} 个批次。"
    ]
    for batch_index, batch in enumerate(run.batches):
        lines.append(f"批次 {batch_index + 1}（{len(batch)} 个）:")
        for tid in batch:
            res = run.results.get(tid)
            if res is None:
                continue
            lines.append(f"  [{res.state}] 任务 {tid} · {res.description}")
            result = (res.result or "").strip()
            if result:
                lines.append(f"      → {result}")
    failed = run.failed_tasks()
    lines.append("")
    if not failed:
        lines.append("整体结论: 全部子任务成功完成。")
    else:
        ids = ", ".join(r.task_id for r in failed)
        lines.append(
            f"整体结论: {len(failed)} 个子任务未成功（{ids}），详见上方。"
        )
    return "\n".join(lines)


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
