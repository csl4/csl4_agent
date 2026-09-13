"""输入侧 Guardrail（宪法 11.1，contracts/guardrails.md §2）。

校验进入 Agent 的用户输入：注入模式 + 违禁词 + 用户追加 ``deny_patterns``。
拦截返回 ``GuardResult(allowed=False)``，调用方（编排 guard_in 节点）中止本轮
并审计留痕（event_type="guardrail_input"，outcome="blocked"）。

确定性优先（宪法 2.1）：规则用代码实现，不依赖大模型审核。
"""

import re
from typing import List, Optional

from pydantic import BaseModel


class GuardResult(BaseModel):
    """输入侧校验结果。"""

    allowed: bool
    reason: str = ""  # 拦截原因（审计用）
    matched_rule: str = ""  # 命中的规则模式
    sanitized: Optional[str] = None  # 可选：脱敏后的输入


# 内置注入模式（常见角色逃逸 / 系统指令覆盖 / 记忆清除）：
#   - 请求忽略/遗忘之前的指令、系统提示、记忆
#   - 声称现在是系统/开发者
#   - 解锁为无限制助手
_DEFAULT_INJECTION_PATTERNS: List[str] = [
    r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions|messages|prompts|context)",
    r"(?i)forget\s+(all\s+)?(your\s+)?(previous\s+)?(instructions|rules|prompts|memory|context)",
    r"(?i)disregard\s+(the\s+)?(above|previous|prior|earlier)\s+(instructions|messages|rules|system|statement|content)",
    r"(?i)you\s+are\s+now\s+(an?\s+)?(the\s+)?system",
    r"(?i)act\s+as\s+(an?\s+)?unrestricted|unfettered|unlimited\s+(assistant|agent)",
    r"(?i)you\s+have\s+no\s+(rules|restrictions|limits|constraints)",
]


class InputGuard:
    """输入侧校验器（注入模式 + 违禁词 + 用户追加 deny_patterns）。"""

    def __init__(self, deny_patterns: Optional[List[str]] = None) -> None:
        raw = list(_DEFAULT_INJECTION_PATTERNS) + list(deny_patterns or [])
        self._patterns: List[re.Pattern] = [re.compile(p) for p in raw]

    def check(self, text: str) -> GuardResult:
        """校验输入；命中任一模式返回拦截结果，否则放行。"""
        if not text or not text.strip():
            return GuardResult(allowed=True)
        for pattern in self._patterns:
            if pattern.search(text):
                return GuardResult(
                    allowed=False,
                    reason="Input blocked by input guard.",
                    matched_rule=pattern.pattern,
                )
        return GuardResult(allowed=True)


__all__ = ["GuardResult", "InputGuard"]
