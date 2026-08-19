"""Context window limiter — checks if compaction is needed.

Monitors the current token usage and triggers compaction when the
conversation approaches the model's context window limit.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ContextWindowLimiter:
    """Checks whether the conversation is approaching the context window limit.

    Uses the LLM's token counting and context window size to determine
    when compaction is needed. Triggers when the estimated token count
    exceeds a configurable threshold ratio of the total window.
    """

    def __init__(
        self,
        llm: Any,
        threshold_ratio: float = 0.75,
        min_messages_before_compact: int = 10,
    ):
        """Initialize the limiter.

        Args:
            llm: LLM instance (must have count_tokens() and
                get_context_window_size() methods).
            threshold_ratio: Token ratio (0.0–1.0) that triggers compaction.
                Default 0.75 means compact when 75% of window is used.
            min_messages_before_compact: Don't compact unless there are at
                least this many messages (avoids compacting short conversations).
        """
        self.llm = llm
        self.threshold_ratio = threshold_ratio
        self.min_messages_before_compact = min_messages_before_compact

    def check_compaction_needed(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Check if the conversation needs compaction.

        Returns True if:
        1. Message count exceeds min_messages_before_compact, AND
        2. Estimated token count exceeds threshold_ratio * context_window_size.

        Args:
            messages: Current conversation messages.
            tools: Optional tool definitions (also consume tokens).

        Returns:
            True if compaction is needed.
        """
        # Don't compact short conversations
        if len(messages) < self.min_messages_before_compact:
            return False

        try:
            current_tokens = self._estimate_tokens(messages, tools)
            max_tokens = self.llm.get_context_window_size()
            threshold = int(max_tokens * self.threshold_ratio)

            needs = current_tokens > threshold
            if needs:
                logger.info(
                    f"Compaction needed: {current_tokens}/{max_tokens} tokens "
                    f"({current_tokens / max_tokens:.1%}), "
                    f"threshold={threshold} ({self.threshold_ratio:.0%})"
                )
            return needs
        except Exception as e:
            logger.warning(f"Token estimation failed: {e}")
            # Fallback: compact based on message count
            return len(messages) > 20

    def should_compact(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Alias for check_compaction_needed()."""
        return self.check_compaction_needed(messages, tools)

    def _estimate_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Estimate the total token count for the current messages and tools.

        Args:
            messages: Current conversation messages.
            tools: Optional tool definitions.

        Returns:
            Estimated token count.
        """
        try:
            usage = self.llm.count_tokens(messages, tools)
            return usage.total_tokens
        except Exception:
            # Rough fallback: ~4 chars per token
            import json

            text = json.dumps(messages, default=str)
            if tools:
                text += json.dumps(tools, default=str)
            return len(text) // 4

    def get_usage_ratio(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        """Get the current token usage ratio (0.0–1.0).

        Args:
            messages: Current conversation messages.
            tools: Optional tool definitions.

        Returns:
            Token usage ratio.
        """
        try:
            current = self._estimate_tokens(messages, tools)
            max_tokens = self.llm.get_context_window_size()
            return min(current / max_tokens, 1.0)
        except Exception:
            return 0.0