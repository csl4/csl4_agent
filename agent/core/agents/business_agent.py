"""业务 Agent：处理业务/领域子任务（FR-001 四角色之一）。

有 LLM 时走 LLM 生成领域结论；无 LLM（或调用失败）时用确定性兜底，
保证离线/测试环境下链路仍可验证（宪法 IV：测试不依赖真实网络）。
"""

import logging
from typing import List, Optional

from agent.core.a2a.protocol import Task, TaskState, set_task_state
from agent.core.agents.base_agent import (
    AgentRole,
    BaseAgent,
    task_input_text,
)
from agent.core.llm import LLM

logger = logging.getLogger(__name__)

BUSINESS_SYSTEM_PROMPT = (
    "你是多Agent 系统中的业务 Agent，负责业务/领域分析类子任务。"
    "请基于给定子任务给出简洁、结构化、可执行的结论。"
)


class BusinessAgent(BaseAgent):
    """业务 Agent：领域/业务子任务处理。"""

    def __init__(
        self,
        agent_id: str = "business",
        llm: Optional[LLM] = None,
        name: str = "",
        parent: Optional[BaseAgent] = None,
    ) -> None:
        super().__init__(agent_id, AgentRole.BUSINESS, name=name or "business", parent=parent)
        self.llm = llm

    def run_task(self, task: Task) -> Task:
        text = task_input_text(task, self.context_messages())
        answer = self._ask_llm(text) if self.llm else ""
        set_task_state(
            task,
            TaskState.TASK_STATE_COMPLETED,
            answer or self._fallback_answer(text),
        )
        return task

    def _ask_llm(self, text: str) -> str:
        try:
            response = self.llm.completion(
                [
                    {"role": "system", "content": BUSINESS_SYSTEM_PROMPT},
                    {"role": "user", "content": text or "(空任务)"},
                ]
            )
            return (response.content or "").strip()
        except Exception as exc:  # noqa: BLE001 - 降级到确定性兜底
            logger.warning("BusinessAgent LLM 调用失败，回退确定性处理: %s", exc)
            return ""

    def _fallback_answer(self, text: str) -> str:
        return (
            "【业务处理结果】\n"
            f"- 子任务: {text or '(空)'}\n"
            "- 结论: 已完成业务分析（当前未启用 LLM，采用确定性兜底结果）。\n"
            "- 建议: 提供领域 LLM 配置以获取智能结论。"
        )


__all__ = ["BusinessAgent", "BUSINESS_SYSTEM_PROMPT"]
