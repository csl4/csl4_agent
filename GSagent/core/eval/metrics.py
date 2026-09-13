"""评估指标（002-enterprise-cli-upgrade US5 T040，FR-013，R-11）。

三指标（对齐 data-model.md EvalResult / SC-007）：
- **完成率**：`normal` 标签案例中 pass 占比（normal 决定完成率基线）。
- **幻觉率**：非 `boundary` 案例中 hallucination 占比
  （多标签归属：boundary 不参与幻觉率分母）。
- **误拒绝率**：全部案例中 false_reject 占比（boundary 只影响误拒绝率）。

另含：错误案例回流（review_cases：非 pass 判定进入评审）+ 基线对比
（compare_baseline，SC-007 无退化）。

输入结果 dict（runner 产出）：
  {"case_id": str, "verdict": "pass"|"hallucination"|"false_reject"|"error",
   "tags": [enum normal|boundary|error]}
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

VERDICTS = ["pass", "hallucination", "false_reject", "error"]
TAGS = ["normal", "boundary", "error"]


@dataclass
class EvalMetrics:
    """一次评估的三指标汇总（to_dict 可 JSON 落盘为基线）。"""

    total: int = 0
    verdict_counts: Dict[str, int] = field(default_factory=dict)
    completion_rate: float = 0.0
    hallucination_rate: float = 0.0
    false_reject_rate: float = 0.0
    normal_count: int = 0
    boundary_count: int = 0
    error_tag_count: int = 0
    group_breakdown: Dict[str, Dict[str, int]] = field(default_factory=dict)
    review_cases: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_metrics(results: List[Dict[str, Any]]) -> EvalMetrics:
    """由结果列表计算三指标（多标签归属规则见模块 docstring / data-model.md）。"""
    m = EvalMetrics(total=len(results))
    m.verdict_counts = {v: 0 for v in VERDICTS}
    m.group_breakdown = {tag: {v: 0 for v in VERDICTS} for tag in TAGS}
    m.normal_count = 0
    m.boundary_count = 0
    m.error_tag_count = 0
    m.review_cases = []

    normal_pass = 0
    non_boundary = 0
    non_boundary_hallucination = 0
    false_reject_total = 0

    for r in results:
        verdict = r.get("verdict", "error")
        tags = r.get("tags") or []
        if verdict not in m.verdict_counts:
            verdict = "error"
        m.verdict_counts[verdict] += 1

        for tag in TAGS:
            if tag in tags:
                m.group_breakdown[tag][verdict] += 1

        if "normal" in tags:
            m.normal_count += 1
            if verdict == "pass":
                normal_pass += 1
        if "boundary" in tags:
            m.boundary_count += 1
        if "error" in tags:
            m.error_tag_count += 1

        # 幻觉率：非 boundary 案例
        if "boundary" not in tags:
            non_boundary += 1
            if verdict == "hallucination":
                non_boundary_hallucination += 1

        if verdict == "false_reject":
            false_reject_total += 1

        if verdict != "pass":
            m.review_cases.append({"case_id": r.get("case_id", ""), "verdict": verdict})

    m.completion_rate = normal_pass / m.normal_count if m.normal_count else 0.0
    m.hallucination_rate = (
        non_boundary_hallucination / non_boundary if non_boundary else 0.0
    )
    m.false_reject_rate = false_reject_total / m.total if m.total else 0.0
    return m


def compare_baseline(
    current: Dict[str, Any], baseline: Dict[str, Any], *, tol: float = 1e-3
) -> List[str]:
    """对比当前指标与基线，返回退化清单（SC-007 无退化）；无退化返回 []。

    完成率低于基线 / 幻觉率高于基线 / 误拒绝率高于基线 均记回归。
    tol 为浮点计算噪声容差（绝对值）。
    """
    regressions: List[str] = []
    if current.get("completion_rate", 0.0) < baseline.get("completion_rate", 0.0) - tol:
        regressions.append(
            "completion_rate {:.3f} < baseline {:.3f}".format(
                current.get("completion_rate", 0.0),
                baseline.get("completion_rate", 0.0),
            )
        )
    if current.get("hallucination_rate", 0.0) > baseline.get("hallucination_rate", 0.0) + tol:
        regressions.append(
            "hallucination_rate {:.3f} > baseline {:.3f}".format(
                current.get("hallucination_rate", 0.0),
                baseline.get("hallucination_rate", 0.0),
            )
        )
    if current.get("false_reject_rate", 0.0) > baseline.get("false_reject_rate", 0.0) + tol:
        regressions.append(
            "false_reject_rate {:.3f} > baseline {:.3f}".format(
                current.get("false_reject_rate", 0.0),
                baseline.get("false_reject_rate", 0.0),
            )
        )
    return regressions


__all__ = ["EvalMetrics", "VERDICTS", "TAGS", "compare_baseline", "compute_metrics"]
