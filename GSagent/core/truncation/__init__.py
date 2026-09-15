"""智能体的上下文摘要压缩（拆壳后：纯函数，节点内调用）。"""

from GSagent.core.truncation.summarizer import (
    DEFAULT_SUMMARY_PROMPT,
    SummaryMeta,
    maybe_summarize,
)

__all__ = ["DEFAULT_SUMMARY_PROMPT", "SummaryMeta", "maybe_summarize"]
