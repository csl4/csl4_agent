"""评估执行（002-enterprise-cli-upgrade US5 T040，FR-013，R-11）。

- run_dataset：对数据集逐条调用 LLM，产出 EvalResult 面
  （case_id/actual/verdict/tags/run_id）。
- run_eval：完整评估 = run_dataset + compute_metrics，返回报告 dict。
- judge_actual：确定性判定器（空输出/拒绝措辞 → false_reject；
  expected 命中 → pass；否则 hallucination）——离线 OfflineLLM 与真实
  LLM 共用；企业验收时专家在 review_cases 回流修正（FR-013）。
- 基线读写：load_baseline / save_baseline（baselines/*.json，SC-007）。

离线/联网切换由调用方决定 LLM 来源（--llm 用 ChatOpenAI（create_chat_model），
否则 OfflineLLM 离线回归）。
"""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from GSagent.core.eval.dataset import EvalCase
from GSagent.core.eval.metrics import compute_metrics

# 拒绝措辞（启发式：输出为空或含拒答标记 → false_reject）
_REFUSAL_MARKERS = (
    "无法",
    "不能",
    "拒绝",
    "做不到",
    "不清楚",
    "无法确定",
    "cannot",
    "can't",
    "unable",
    "sorry",
    "抱歉",
)


class OfflineLLM:
    """离线回归打桩（CLI 侧 `agent eval run` 无 `--llm` 时的默认执行器）。

    与测试桩等价的最小形态（agent 包不反向依赖 tests/）：可注入预置回复，
    耗尽后返回 "(fallback)"——离线冒烟验证数据集加载 + 指标计算 + 基线对比
    整条管线。
    """

    def __init__(self, responses: Optional[List[str]] = None) -> None:
        self.responses = list(responses or [])
        self.calls: List[Any] = []

    def completion(self, messages, **kwargs) -> Any:
        self.calls.append(messages)
        if self.responses:
            return SimpleNamespace(content=self.responses.pop(0))
        return SimpleNamespace(content="(fallback)")


def judge_actual(actual: Any, expected: str) -> str:
    """把单条实际输出判为 pass/hallucination/false_reject。

    空输出或拒答措辞 → false_reject；expected 出现在输出中 → pass；
    否则 → hallucination（确定性启发式，供离线回归与基线对比）。
    """
    text = str(actual or "").strip()
    if not text:
        return "false_reject"
    low = text.lower()
    if any(marker in low for marker in _REFUSAL_MARKERS):
        return "false_reject"
    if expected in text:
        return "pass"
    return "hallucination"


def _llm_content(llm: Any, messages: List[Dict[str, Any]]) -> str:
    """兼容 BaseChatModel（invoke）与 OfflineLLM/旧 LLM（completion）。

    ``agent eval --llm`` 用 ChatOpenAI（langchain BaseChatModel，002-langchain-
    ecosystem）；默认离线回归用 OfflineLLM（completion）。鸭子类型统一取 content。
    """
    invoke = getattr(llm, "invoke", None)
    if invoke is not None:
        from GSagent.core.llm_adapter import dict_to_messages

        resp = invoke(dict_to_messages(messages))
        return getattr(resp, "content", "") or ""
    resp = llm.completion(messages)
    return getattr(resp, "content", "") or ""


def run_dataset(
    dataset: List[EvalCase], llm: Any, *, run_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """对每条案例调用 LLM，返回 EvalResult 面（单条失败落 error 不中断）。"""
    run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
    results: List[Dict[str, Any]] = []
    for case in dataset:
        actual = ""
        try:
            actual = _llm_content(llm, [{"role": "user", "content": case.prompt}])
            verdict = judge_actual(actual, case.expected)
        except Exception:  # noqa: BLE001 - 单条执行失败落 error，整批继续
            verdict = "error"
        results.append(
            {
                "case_id": case.case_id,
                "actual": actual,
                "verdict": verdict,
                "tags": list(case.tags),
                "run_id": run_id,
            }
        )
    return results


def run_eval(
    dataset: List[EvalCase], llm: Any, *, run_id: Optional[str] = None
) -> Dict[str, Any]:
    """完整评估：执行 + 指标汇总，返回报告 dict（含 review_cases 回流）。"""
    results = run_dataset(dataset, llm, run_id=run_id)
    metrics = compute_metrics(results)
    report = {
        "run_id": results[0]["run_id"] if results else "run-empty",
        "total": metrics.total,
        "metrics": metrics.to_dict(),
        "results": results,
    }
    return report


def load_baseline(path: Any) -> Optional[Dict[str, Any]]:
    """读基线 JSON（metrics.to_dict() 形态）；不存在/损坏返回 None。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_baseline(path: Any, metrics_dict: Dict[str, Any]) -> None:
    """写基线 JSON（供 agent eval baseline 建立/更新，SC-007）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(metrics_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = [
    "judge_actual",
    "load_baseline",
    "run_dataset",
    "run_eval",
    "save_baseline",
]
