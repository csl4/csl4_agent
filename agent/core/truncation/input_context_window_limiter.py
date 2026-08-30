"""上下文窗口限制器 —— 检查是否需要压缩。

监控当前 token 用量，当对话接近模型的上下文窗口上限时触发压缩。
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---- 行为对象：上下文窗口「体检器」----
# 输入：messages(+tools)；输出：布尔——是否已逼近上下文上限、需要触发压缩。
# 设计要点：token 估算统一委托给 LLM.count_tokens()（那里持有 ~4 字符/token 的兜底），
#           本类不再重复实现；超阈值比(默认0.75)即触发 compaction。
class ContextWindowLimiter:
    """检查对话是否已接近上下文窗口上限。

    使用 LLM 的 token 计数和上下文窗口大小来判断
    何时需要压缩。当估算的 token 数超过总窗口的可配置阈值比例时触发。
    """

    def __init__(
        self,
        llm: Any,
        threshold_ratio: float = 0.75,
        min_messages_before_compact: int = 10,
    ):
        """初始化限制器。

        参数:
            llm: LLM 实例（必须具有 count_tokens() 和
                get_context_window_size() 方法）。
            threshold_ratio: 触发压缩的 token 比例（0.0–1.0）。
                默认 0.75 表示窗口用到 75% 时压缩。
            min_messages_before_compact: 消息数量达到该值之前不压缩
                （避免压缩过短的对话）。
        """
        self.llm = llm
        self.threshold_ratio = threshold_ratio
        self.min_messages_before_compact = min_messages_before_compact

    def check_compaction_needed(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """检查对话是否需要压缩。

        在以下条件满足时返回 True：
        1. 消息数量超过 min_messages_before_compact，且
        2. 估算的 token 数超过 threshold_ratio * context_window_size。

        参数:
            messages: 当前的对话消息。
            tools: 可选的工具定义（同样会消耗 token）。

        返回:
            如果需要压缩则返回 True。
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
        """check_compaction_needed() 的别名。"""
        return self.check_compaction_needed(messages, tools)

    def _estimate_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """估算当前消息和工具的 token 总数。

        完全委托给 LLM 的 count_tokens()，由其持有粗略估算的兜底逻辑。
        任何异常都会向上抛给调用方，
        由 check_compaction_needed()/get_usage_ratio() 应用各自的兜底方案。

        参数:
            messages: 当前的对话消息。
            tools: 可选的工具定义。

        返回:
            估算的 token 数量。
        """
        usage = self.llm.count_tokens(messages, tools)
        return usage.total_tokens

    def get_usage_ratio(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        """获取当前的 token 使用比例（0.0–1.0）。

        参数:
            messages: 当前的对话消息。
            tools: 可选的工具定义。

        返回:
            token 使用比例。
        """
        try:
            current = self._estimate_tokens(messages, tools)
            max_tokens = self.llm.get_context_window_size()
            return min(current / max_tokens, 1.0)
        except Exception:
            return 0.0