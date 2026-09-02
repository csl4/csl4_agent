"""主 Agent：现有单 Agent 的包装 + 多Agent 编排触发（FR-001 四角色之一）。

职责：
  ① 包装既有 ToolCallingLLM（单 Agent 行为保持不变，宪法 II）；
  ② 多Agent 模式下触发编排（Orchestrator）并做终局归纳；
  ③ 对 CLI 暴露与 ToolCallingLLM 一致的 `call_stream()`，多Agent 事件
     向后兼容地并入 StreamMessage 事件流（T017/T018）。

调用方式：
- `run_task(task)`：无头模式（测试/程序化调用），同步返回终止态 Task。
- `call_stream(messages, ...)`：CLI 模式，逐条 yield StreamMessage。
"""

import logging
from typing import Any, Dict, Generator, List, Optional

from agent.core.a2a.client import A2AClientError, InProcessA2AClient
from agent.core.a2a.protocol import Task, TaskState, set_task_state
from agent.core.agents.base_agent import (
    AgentRole,
    BaseAgent,
    task_input_text,
    task_result_text,
)
from agent.core.agents.orchestrator import Orchestrator
from agent.core.llm import LLM
from agent.core.skills.library import SkillLibrary, format_skills_block
from agent.core.tool_calling_llm import ToolCallingLLM
from agent.utils.stream import StreamEvents, StreamMessage

logger = logging.getLogger(__name__)

MAIN_SYSTEM_PROMPT = (
    "你是多Agent 系统的用户主接口。请把编排层返回的各子任务结果，"
    "归纳为一条面向用户的、结构化自然语言最终答复。"
)


class MainAgent(BaseAgent):
    """主 Agent：用户交互 + 整体调度 + 终局决策。"""

    def __init__(
        self,
        agent_id: str = "main",
        tool_calling_llm: Optional[ToolCallingLLM] = None,
        orchestrator: Optional[Orchestrator] = None,
        llm: Optional[LLM] = None,
        name: str = "",
        parent: Optional[BaseAgent] = None,
        skill_library: Optional[SkillLibrary] = None,
        knowledge_text: str = "",
    ) -> None:
        super().__init__(
            agent_id,
            AgentRole.MAIN,
            name=name or "main",
            parent=parent,
            knowledge_text=knowledge_text,
        )
        self.tool_calling_llm = tool_calling_llm
        self.orchestrator = orchestrator
        self.llm = llm
        # US3 FR-006/007：技能库与环境知识，注入终局归纳 LLM 系统提示。
        self.skill_library = skill_library

    @property
    def multi_agent(self) -> bool:
        """是否处于多Agent 模式（装配了编排 Agent）。"""
        return self.orchestrator is not None

    # ---- 无头模式（测试/程序化调用）----
    def run_task(self, task: Task) -> Task:
        text = task_input_text(task, self.context_messages())
        if not self.multi_agent:
            return self._run_single_fallback(task, text)
        merged = self._orchestrate(text)
        final_text = self._finalize(text, merged)
        set_task_state(task, TaskState.TASK_STATE_COMPLETED, final_text)
        return task

    def _run_single_fallback(self, task: Task, text: str) -> Task:
        """无编排 Agent 时退化为单 Agent 直答（保持链路易验证）。"""
        set_task_state(
            task,
            TaskState.TASK_STATE_COMPLETED,
            f"（单Agent 模式，未装配编排）收到任务: {text or '(空)'}",
        )
        return task

    def _orchestrate(self, text: str) -> str:
        """触发编排，返回编排层归并后的结果文本。"""
        client = InProcessA2AClient(self.orchestrator)
        try:
            result = client.send_task(text)
            return task_result_text(result)
        except A2AClientError as exc:
            logger.warning("编排失败: %s", exc)
            return f"编排失败: {exc}"

    def _finalize(self, text: str, merged: str) -> str:
        """终局决策：LLM 归纳；无 LLM/失败时回退归并原文。"""
        if self.llm is None:
            return merged
        system = MAIN_SYSTEM_PROMPT
        if self.skill_library is not None:
            block = format_skills_block(self.skill_library.match(text))
            if block:
                system += "\n\n" + block
        if self.knowledge_text:
            system += "\n\n" + self.knowledge_text
        try:
            response = self.llm.completion(
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": f"原始任务: {text}\n\n编排结果:\n{merged}",
                    },
                ]
            )
            return (response.content or "").strip() or merged
        except Exception as exc:  # noqa: BLE001 - 兜底返回归并原文
            logger.warning("MainAgent 终局归纳失败，回退归并原文: %s", exc)
            return merged

    # ---- CLI 模式（与 ToolCallingLLM.call_stream 同签名，可被 _run_turn 消费）----
    def call_stream(
        self,
        messages: List[Dict[str, Any]],
        enable_tool_approval: bool = False,
        tool_decisions: Optional[Dict[str, bool]] = None,
        frontend_tool_results: Optional[Dict[str, Any]] = None,
        request_context: Optional[Dict[str, Any]] = None,
        cancel_event: Any = None,
        tool_number_offset: int = 0,
        iteration_offset: int = 0,
    ) -> Generator[StreamMessage, None, None]:
        if not self.multi_agent:
            if self.tool_calling_llm is not None:
                yield from self.tool_calling_llm.call_stream(
                    messages=messages,
                    enable_tool_approval=enable_tool_approval,
                    tool_decisions=tool_decisions,
                    frontend_tool_results=frontend_tool_results,
                    request_context=request_context,
                    cancel_event=cancel_event,
                    tool_number_offset=tool_number_offset,
                    iteration_offset=iteration_offset,
                )
            else:
                user_text = self._last_user_text(messages)
                yield StreamMessage(
                    event=StreamEvents.ANSWER_END,
                    data={"content": user_text or "(空)", "messages": list(messages)},
                )
            return

        user_text = self._last_user_text(messages)
        yield StreamMessage(
            event=StreamEvents.MULTI_AGENT_DECOMPOSE, data={"task": user_text}
        )

        merged = self._orchestrate(user_text)
        for record in self._subtask_events():
            yield StreamMessage(
                event=StreamEvents.MULTI_AGENT_SUBAGENT, data=record
            )

        final_text = self._finalize(user_text, merged)
        yield StreamMessage(
            event=StreamEvents.MULTI_AGENT_DONE,
            data={"content": final_text, "task": user_text},
        )
        yield StreamMessage(
            event=StreamEvents.ANSWER_DELTA, data={"content": final_text}
        )
        yield StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={"content": final_text, "messages": list(messages)},
        )

    def _subtask_events(self) -> List[Dict[str, Any]]:
        """把最近一次编排的子任务明细转成流事件数据。"""
        orchestrator = self.orchestrator
        if orchestrator is None:
            return []
        return [
            {
                "index": r["index"],
                "kind": r["kind"],
                "text": r["text"],
                "worker": r["worker"],
                "state": r["state"],
            }
            for r in orchestrator.last_records
        ]

    @staticmethod
    def _last_user_text(messages: List[Dict[str, Any]]) -> str:
        """从 OpenAI 风格消息里取最后一个 user 消息的文本。"""
        for msg in reversed(messages):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content") or ""
            if isinstance(content, list):
                return " ".join(
                    str(c.get("text", ""))
                    for c in content
                    if isinstance(c, dict)
                ).strip()
            return str(content)
        return ""


__all__ = ["MAIN_SYSTEM_PROMPT", "MainAgent"]
