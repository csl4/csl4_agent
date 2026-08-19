"""Built-in tool result transformers."""

import json
import logging
from typing import Any, Dict, List, Optional

from agent.core.models import StructuredToolResult, StructuredToolResultStatus
from agent.core.tools import Transformer

logger = logging.getLogger(__name__)


class JsonTruncationTransformer(Transformer):
    """Truncates large JSON tool results to fit within token limits.

    When a tool result's JSON data exceeds max_tokens, this transformer
    truncates it by keeping only the first N items (for lists) or
    the first N keys (for dicts), and adds a truncation notice.
    """

    max_tokens: int = 4000
    max_list_items: int = 50
    max_dict_keys: int = 30

    def transform(self, result: StructuredToolResult) -> StructuredToolResult:
        """Truncate the result data if it's too large.

        Args:
            result: The tool result to transform.

        Returns:
            Transformed result with truncated data if needed.
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
        """Truncate data to fit within max_tokens.

        Args:
            data: The data to truncate (dict, list, or scalar).

        Returns:
            Truncated data with a truncation notice.
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
    """Limits the number of lines in a text result.

    When a tool result's text output exceeds max_lines, this transformer
    truncates it and adds a truncation notice.
    """

    max_lines: int = 200

    def transform(self, result: StructuredToolResult) -> StructuredToolResult:
        """Truncate text result if it exceeds max_lines.

        Args:
            result: The tool result to transform.

        Returns:
            Transformed result with truncated text if needed.
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