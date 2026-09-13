"""评估数据集加载（002-enterprise-cli-upgrade US5 T040，R-11）。

EvalCase YAML 格式（eval/datasets/*.yaml，对齐 data-model.md EvalCase）：
  - case_id: str      样本 ID（唯一）
  - prompt:  str      输入
  - expected: str     期望结果
  - tags:    list[normal|boundary|error]  标签（多标签归属规则见 metrics.py）

校验：case_id 唯一、prompt 非空、expected 存在、tags 合法枚举。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

import yaml

from GSagent.core.eval.metrics import TAGS


class EvalDatasetError(ValueError):
    """数据集文件格式/内容非法。"""


@dataclass
class EvalCase:
    case_id: str
    prompt: str
    expected: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "prompt": self.prompt,
            "expected": self.expected,
            "tags": list(self.tags),
        }


def load_dataset(path: Any) -> List[EvalCase]:
    """加载 YAML 数据集并校验（非法 → EvalDatasetError）。"""
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvalDatasetError(f"数据集不可读: {p}") from exc
    except yaml.YAMLError as exc:
        raise EvalDatasetError(f"数据集 YAML 解析失败: {p}: {exc}") from exc

    if not isinstance(raw, list):
        raise EvalDatasetError(f"数据集顶层必须是 list: {p}")
    return _validate(raw)


def _validate(raw: Sequence[Any]) -> List[EvalCase]:
    cases: List[EvalCase] = []
    seen: set = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalDatasetError(f"第 {i} 条不是对象: {item!r}")
        case_id = str(item.get("case_id", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        expected = str(item.get("expected", "")).strip()
        tags = list(item.get("tags") or [])
        if not case_id:
            raise EvalDatasetError(f"第 {i} 条缺少 case_id")
        if case_id in seen:
            raise EvalDatasetError(f"case_id 重复: {case_id}")
        if not prompt:
            raise EvalDatasetError(f"case {case_id} 的 prompt 为空")
        if not expected:
            raise EvalDatasetError(f"case {case_id} 的 expected 为空")
        for tag in tags:
            if tag not in TAGS:
                raise EvalDatasetError(f"case {case_id} 的非法标签: {tag}")
        seen.add(case_id)
        cases.append(EvalCase(case_id=case_id, prompt=prompt, expected=expected, tags=tags))
    return cases


__all__ = ["EvalCase", "EvalDatasetError", "load_dataset"]
