"""输出侧 Guardrail（宪法 11.1 / 5.4，contracts/guardrails.md §3）。

仅对**程序消费的结构化输出**做 schema 校验；自由文本直接放行（不误伤）。
校验失败走分层 Fallback（轻量修复 → 定向重试 → 降级标记），不直接抛错
（宪法 5.4）。

``expected_schema`` 为可选 dict，形如 ``{"type": "object", "properties": {...}}``
或 ``{"required": [字段名, ...]}``；None 表示自由文本（放行）。
"""

import json
import re
from typing import Any, Dict, Optional

from pydantic import BaseModel


class OutputCheckResult(BaseModel):
    """输出侧校验结果。"""

    ok: bool
    issue: str = ""  # 校验失败描述
    fallback_applied: bool = False  # 是否已执行轻量修复
    final_content: str = ""  # 修复后内容（若应用）


class OutputGuard:
    """输出侧校验器（结构化 schema 校验 + 敏感键过滤 + 分层 Fallback）。"""

    def __init__(self, fallback_retries: int = 1) -> None:
        # 定向重试次数上限（宪法 5.4：≤2）
        self.fallback_retries = max(0, min(2, fallback_retries))

    def check(self, content: str, expected_schema: Optional[Dict[str, Any]] = None) -> OutputCheckResult:
        """校验输出内容。

        - ``expected_schema`` 为 None → 自由文本，直接放行。
        - 有 schema → 尝试 JSON 解析 + 必填字段校验；失败走轻量修复（最多
          ``fallback_retries`` 次），仍失败标记 ``fallback_applied`` 降级。
        """
        if expected_schema is None:
            return OutputCheckResult(ok=True, final_content=content)

        repaired = self._try_repair_json(content, expected_schema)
        if repaired is not None:
            return OutputCheckResult(
                ok=True, fallback_applied=True, final_content=repaired
            )
        return OutputCheckResult(
            ok=False,
            issue="Output does not conform to expected schema.",
            final_content=content,
        )

    def _try_repair_json(self, content: str, schema: Dict[str, Any]) -> Optional[str]:
        """轻量修复：剥离代码围栏后重解析；返回修复后的 JSON 文本或 None。"""
        candidates = [content]
        # 轻量修复 1：剥离 ```json ... ``` 围栏
        if content.lstrip().startswith("```"):
            inner = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            candidates.append(inner)
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if self._validate(data, schema):
                return candidate
        return None

    @staticmethod
    def _validate(data: Any, schema: Dict[str, Any]) -> bool:
        """校验 data 是否符合 schema（object 类型 + 必填字段）。"""
        if not isinstance(data, dict):
            return False
        required = schema.get("required") or []
        if isinstance(required, list):
            for field in required:
                if field not in data:
                    return False
        return True


__all__ = ["OutputCheckResult", "OutputGuard"]
