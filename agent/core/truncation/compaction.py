"""对话压缩 —— 对较早的消息做摘要，以节省上下文窗口空间。

策略：
1. 保持系统提示词不变。
2. 保留最后 `keep_last_n` 条消息（最近的对话轮次）。
3. 将较早的消息交给 LLM 进行摘要。
4. 在系统提示词之后，把摘要作为一条系统消息插入。
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


# ---- 行为对象：对话压缩器 ----
# 输入：过长的 messages 列表；输出：更短的 messages（旧消息被摘要成一条 system 消息）。
# 设计要点：保系统提示词 + 最近的 keep_last_n 条，其余交给 LLM 摘要；
#           就有个细节——压缩边界若恰好落在 assistant.tool_calls 消息后，会被拉进保留区，
#           避免「工具结果悬空、下轮 LLM 调用因 orphaned tool_call 被拒」。
class ConversationCompactor:
    """通过摘要较早的消息来压缩过长的对话历史。

    使用 LLM 为较早的消息生成简洁摘要，
    用一条系统消息替换它们，以节省上下文窗口空间。
    """

    def __init__(
        self,
        llm: Any,
        compaction_prompt: Optional[str] = None,
        keep_last_n: int = 6,
    ):
        """初始化压缩器。

        参数:
            llm: LLM 实例（必须具有 completion() 方法）。
            compaction_prompt: 用于摘要的自定义提示词模板。
                必须包含 `{conversation}` 占位符。
            keep_last_n: 保留不压缩的最近消息条数。
        """
        self.llm = llm
        self.compaction_prompt = compaction_prompt or DEFAULT_COMPACTION_PROMPT
        self.keep_last_n = keep_last_n

    def compact(
        self,
        messages: List[Dict[str, Any]],
        keep_last_n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """压缩对话历史以减少 token 数量。

        保持系统提示词和最近的消息不变。
        将较早的消息摘要成一条系统级摘要。

        参数:
            messages: 完整的对话消息列表。
            keep_last_n: 本次调用对默认 keep_last_n 的覆盖值。

        返回:
            更短的消息列表，较早的消息被替换为摘要。
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

        # Don't split an assistant tool_calls message from its tool result
        # messages: if the boundary lands right after such an assistant
        # message (assistant summarized, its tool results kept), the kept
        # tool messages dangle without their request and providers reject
        # the next LLM call. Pull the assistant message into the keep zone.
        while (
            summarize_end > summarize_start
            and messages[summarize_end - 1].get("role") == "assistant"
            and messages[summarize_end - 1].get("tool_calls")
        ):
            summarize_end -= 1

        if summarize_start >= summarize_end:
            return messages

        to_summarize = messages[summarize_start:summarize_end]

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
        """使用 LLM 为给定消息生成摘要。

        参数:
            messages: 需要摘要的消息列表。

        返回:
            一段简洁的摘要字符串。
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
        """将消息列表格式化为可供摘要的可读文本。

        参数:
            messages: 聊天消息列表。

        返回:
            格式化后的对话文本。
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