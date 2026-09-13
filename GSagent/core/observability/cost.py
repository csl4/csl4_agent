"""本地定价成本估算（US2 T021，R-06，FR-007）。

配置段 `cost.pricing`：``{model: {prompt_per_1k, completion_per_1k}}``（每 1k token 美元）。
无定价条目 → 只记 token 不估金额（离线一致，不依赖任何远程定价服务）。

模型名匹配：
1. 精确匹配完整模型名（如 ``deepseek/deepseek-v4-flash``）；
2. 回退匹配最后一个 ``/`` 之后的部分（``deepseek-v4-flash``），便于以
   「供应商前缀 + 模型名」命名的 LiteLLM 模型复用同一行定价。
"""

from typing import Any, Dict, Optional


class CostEstimator:
    """按本地定价表把 token 用量折算成估算成本。"""

    def __init__(self, pricing: Optional[Dict[str, Any]] = None) -> None:
        self.pricing: Dict[str, Any] = pricing or {}

    def estimate(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """估算一次调用的成本（美元）；无定价条目返回 0.0。"""
        entry = self.pricing.get(model) or self.pricing.get(model.rsplit("/", 1)[-1])
        if not entry:
            return 0.0
        prompt_per_1k = float(entry.get("prompt_per_1k", 0) or 0)
        completion_per_1k = float(entry.get("completion_per_1k", 0) or 0)
        return (
            prompt_tokens / 1000 * prompt_per_1k
            + completion_tokens / 1000 * completion_per_1k
        )

    def __bool__(self) -> bool:
        """是否有任何定价条目（空表 = 不估算金额，R-06）。"""
        return bool(self.pricing)


__all__ = ["CostEstimator"]
