"""内置的工具结果变换器。"""


# ======================= 中文导览 =======================
# 内置的 Transformer 实现：挂到 Tool.transformers 上，在工具成功后自动应用，
#   防止超大结果把上下文撑爆。
#   JsonTruncationTransformer → 对 JSON 数据，超限时截断 list/dict（保留前 N 项并加截断说明）。
#   LineCountTransformer       → 对文本数据，超过 max_lines 行时截断并加说明。
# 设计要点：这是「结果瘦身」这类横切关注点与工具逻辑解耦的落地样例。
# =========================================================

import json
import logging
from typing import Any, Dict, List, Optional

from agent.core.models import StructuredToolResult, StructuredToolResultStatus
from agent.core.tools import Transformer

logger = logging.getLogger(__name__)


class JsonTruncationTransformer(Transformer):
    """截断过大的 JSON 工具结果，使其适配 token 限制。

    当工具结果的 JSON 数据超过 max_tokens 时，本变换器会进行截断：
    对于 list 仅保留前 N 项，对于 dict 仅保留前 N 个键，并附加截断说明。
    """

    max_tokens: int = 4000
    max_list_items: int = 50
    max_dict_keys: int = 30

    def transform(self, result: StructuredToolResult) -> StructuredToolResult:
        """若结果数据过大则进行截断。

        参数:
            result: 要变换的工具结果。

        返回:
            变换后的结果，必要时包含被截断的数据。
        """
        if result.status != StructuredToolResultStatus.SUCCESS:
            return result

        if result.data is None:
            return result

        try:
            data_str = json.dumps(result.data, ensure_ascii=False, default=str)
            # Rough estimate: ~4 chars per token
            estimated_tokens = len(data_str) // 4

            if estimated_tokens <= self.max_tokens:
                return result

            logger.info(
                f"Truncating tool result: ~{estimated_tokens} tokens → "
                f"target ~{self.max_tokens} tokens"
            )

            truncated = self._truncate_data(result.data)
            result.data = truncated
            return result

        except Exception as e:
            logger.warning(f"JsonTruncationTransformer failed: {e}")
            return result

    def _truncate_data(self, data: Any) -> Any:
        """截断数据以适配 max_tokens。

        参数:
            data: 要截断的数据（dict、list 或标量）。

        返回:
            截断后的数据，附截断说明。
        """
        if isinstance(data, list):
            if len(data) > self.max_list_items:
                return {
                    "items": data[:self.max_list_items],
                    "_truncated": True,
                    "_original_count": len(data),
                    "_message": (
                        f"Result truncated: showing {self.max_list_items} of "
                        f"{len(data)} items. Use more specific filters to narrow results."
                    ),
                }
            return data

        if isinstance(data, dict):
            if len(data) > self.max_dict_keys:
                keys = list(data.keys())
                truncated_dict = {
                    k: data[k] for k in keys[:self.max_dict_keys]
                }
                truncated_dict["_truncated"] = True
                truncated_dict["_original_key_count"] = len(data)
                truncated_dict["_message"] = (
                    f"Result truncated: showing {self.max_dict_keys} of "
                    f"{len(data)} keys."
                )
                return truncated_dict
            return data

        return data


class LineCountTransformer(Transformer):
    """限制文本结果的行数。

    当工具结果的文本输出超过 max_lines 时，本变换器会进行截断并附加截断说明。
    """

    max_lines: int = 200

    def transform(self, result: StructuredToolResult) -> StructuredToolResult:
        """若文本结果超过 max_lines 则进行截断。

        参数:
            result: 要变换的工具结果。

        返回:
            变换后的结果，必要时包含被截断的文本。
        """
        if result.status != StructuredToolResultStatus.SUCCESS:
            return result

        if result.data is None:
            return result

        # Only process string data
        if not isinstance(result.data, str):
            return result

        lines = result.data.split("\n")
        if len(lines) <= self.max_lines:
            return result

        logger.info(
            f"Truncating text result: {len(lines)} lines → {self.max_lines} lines"
        )

        truncated = "\n".join(lines[:self.max_lines])
        result.data = (
            f"{truncated}\n\n"
            f"[... {len(lines) - self.max_lines} more lines truncated ...]\n"
            f"[Total: {len(lines)} lines, showing first {self.max_lines}]"
        )
        return result