"""Conversation compaction — summarizes older messages to save context window space.

Strategy:
1. Keep the system prompt intact.
2. Keep the last `keep_last_n` messages (most recent conversation turns).
3. Send the older messages to an LLM for summarization.
4. Insert the summary as a system message after the system prompt.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_COMPACTION_PROMPT = (
    "Please summarize the following conversation history concisely. "
    "Focus on: key facts discovered, decisions made, tool calls and their results, "
    "and any unresolved questions. "
    "Keep the summary brief but don't lose important context.\n\n"
    "Conversation:\n{conversation}"
)


class ConversationCompactor:
    """Compacts long conversation histories by summarizing older messages.

    Uses an LLM to generate a concise summary of older messages,
    replacing them with a single system message to save context window space.
    """

    def __init__(
        self,
        llm: Any,
        compaction_prompt: Optional[str] = None,
        keep_last_n: int = 6,
    ):
        """Initialize the compactor.

        Args:
            llm: LLM instance (must have a completion() method).
            compaction_prompt: Custom prompt template for summarization.
                Must contain `{conversation}` placeholder.
            keep_last_n: Number of most recent messages to keep uncompressed.
        """
        self.llm = llm
        self.compaction_prompt = compaction_prompt or DEFAULT_COMPACTION_PROMPT
        self.keep_last_n = keep_last_n

    def compact(
        self,
        messages: List[Dict[str, Any]],
        keep_last_n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Compact conversation history to reduce token count.

        Keeps the system prompt and the most recent messages intact.
        Summarizes older messages into a single system-level summary.

        Args:
            messages: The full conversation message list.
            keep_last_n: Override the default keep_last_n for this call.

        Returns:
            A shorter message list with older messages replaced by a summary.
        """
        if keep_last_n is None:
            keep_last_n = self.keep_last_n

        total = len(messages)

        # If messages are already short enough, don't compact
        if total <= keep_last_n + 2:
            return messages

        # Identify what to keep and what to summarize
        system_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "system"
        ]

        # Messages to summarize: everything between system prompts and
        # the last keep_last_n messages, excluding system prompts
        summarize_start = 0
        if system_indices:
            # Start after the last system prompt that's before the keep zone
            for idx in system_indices:
                if idx < total - keep_last_n:
                    summarize_start = idx + 1
                else:
                    break

        summarize_end = total - keep_last_n

        if summarize_start >= summarize_end:
            return messages

        to_summarize = messages[summarize_start:summarize_end]
        to_keep = messages[:summarize_start] + messages[summarize_end:]

        # Filter out pure system messages from keep zone
        # (they'll be replaced by the summary)
        to_keep = [m for m in to_keep if m.get("role") != "system" or messages.index(m) < summarize_start]

        # Generate summary
        summary = self._summarize(to_summarize)

        # Build the compacted message list:
        # system prompt → summary → recent messages
        compacted: List[Dict[str, Any]] = []

        # Keep the original system prompt
        system_msgs = [m for m in messages if m.get("role") == "system"]
        if system_msgs:
            compacted.append(system_msgs[0])

        # Insert the summary
        compacted.append({
            "role": "system",
            "content": f"[Previous conversation summary]\n{summary}",
        })

        # Add the recent messages (excluding system prompts)
        recent = [
            m for m in messages[summarize_end:]
            if m.get("role") != "system"
        ]
        compacted.extend(recent)

        logger.info(
            f"Compacted conversation: {total} messages → {len(compacted)} messages"
        )
        return compacted

    def _summarize(self, messages: List[Dict[str, Any]]) -> str:
        """Generate a summary of the given messages using the LLM.

        Args:
            messages: List of messages to summarize.

        Returns:
            A concise summary string.
        """
        # Format messages as readable text
        conversation_text = self._format_messages(messages)

        prompt = self.compaction_prompt.format(conversation=conversation_text)

        try:
            response = self.llm.completion(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                stream=False,
            )
            return response.content or "(summary unavailable)"
        except Exception as e:
            logger.warning(f"Compaction summarization failed: {e}")
            # Fallback: return a simple truncation message
            return (
                f"Earlier conversation ({len(messages)} messages) omitted "
                f"due to context window limits."
            )

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        """Format a list of messages as readable text for summarization.

        Args:
            messages: List of chat messages.

        Returns:
            Formatted conversation text.
        """
        lines: List[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "tool":
                # Truncate long tool results
                content_str = str(content)
                if len(content_str) > 500:
                    content_str = content_str[:500] + "..."
                lines.append(f"[{role}] {content_str}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tool_names = [
                        tc.get("function", {}).get("name", "?")
                        for tc in tool_calls
                    ]
                    lines.append(
                        f"[assistant] Called tools: {', '.join(tool_names)}"
                    )
                if content:
                    content_str = str(content)
                    if len(content_str) > 300:
                        content_str = content_str[:300] + "..."
                    lines.append(f"[assistant] {content_str}")
            else:
                content_str = str(content) if content else ""
                if len(content_str) > 300:
                    content_str = content_str[:300] + "..."
                lines.append(f"[{role}] {content_str}")

        return "\n".join(lines)