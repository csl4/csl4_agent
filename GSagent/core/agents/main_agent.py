"""主 Agent：多Agent 编排的用户主接口 + 终局归纳（FR-001 四角色之一）。

职责：
  ① 触发编排（Orchestrator 拆解/并行调度）并做终局归纳；
  ② 对 CLI 暴露与 ToolCallingLLM 一致的 `call_stream()`，多Agent 事件
     向后兼容地并入 StreamMessage 事件流（T017/T018）；
  ③ 未装配编排时退化为单 Agent 兜底直答（防御性，生产单 Agent 走
     ToolCallingLLM 直连，不经过本类）。

调用方式：
- `run_task(task)`：无头模式（测试/程序化调用），同步返回终止态 Task。
- `call_stream(messages, ...)`：CLI 模式，逐条 yield StreamMessage。
"""

import json
import logging
from typing import Any, Dict, Generator, List, Optional, Tuple

from GSagent.core.a2a.client import A2AClientError, InProcessA2AClient
from GSagent.core.a2a.protocol import Task, TaskState, set_task_state
from GSagent.core.agents.base_agent import (
    AgentRole,
    BaseAgent,
    message_text,
    subtask_dicts,
    task_input_text,
    task_result_text,
)
from GSagent.core.agents.orchestrator import Orchestrator, merge_results
from GSagent.core.models import StructuredToolResultStatus, ToolInvokeContext
from GSagent.core.providers import LLM
from GSagent.core.skills.library import SkillLibrary, format_skills_block
from GSagent.utils.stream import StreamEvents, StreamMessage

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
        self.orchestrator = orchestrator
        self.llm = llm
        # US3 FR-006/007：技能库与环境知识，注入终局归纳 LLM 系统提示。
        self.skill_library = skill_library
        # 审批回传：阶段 1 暂停时保存的编排上下文（阶段 2 恢复用）。
        self._approval_ctx: Optional[Dict[str, Any]] = None

    @property
    def multi_agent(self) -> bool:
        """是否处于多Agent 模式（装配了编排 Agent）。"""
        return self.orchestrator is not None

    # ---- 无头模式（测试/程序化调用）----
    def run_task(self, task: Task) -> Task:
        text = task_input_text(task, self)
        if not self.multi_agent:
            return self._run_single_fallback(task, text)
        _, final_text, _ = self._run_orchestration(text)
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

    def _run_orchestration(self, text: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """执行完整编排流水线：触发编排 + 终局归纳。

        返回 ``(merged, final_text, pending_approvals)``：pending_approvals 是
        命令子任务需要人工审批的请求列表（CLI 模式据此暂停回传用户）。

        无头模式（run_task）与 CLI 模式（call_stream）共用同一条编排管道，
        避免两处调用链漂移（A3 去重）。
        """
        merged = self._orchestrate(text)
        pending: List[Dict[str, Any]] = []
        if self.orchestrator is not None:
            pending = list(
                getattr(self.orchestrator, "last_pending_approvals", None) or []
            )
        return merged, self._finalize(text, merged), pending

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
    # 两阶段执行（审批回传）：阶段 1 跑编排并收集待审批命令，若存在则 yield
    # APPROVAL_REQUIRED 暂停让 CLI 弹窗（tool_decisions 回填后阶段 2 恢复）；
    # 无待审批时与改前一致，一次产出最终结果。
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
        user_text = self._last_user_text(messages)
        yield StreamMessage(
            event=StreamEvents.MULTI_AGENT_DECOMPOSE, data={"task": user_text}
        )

        # 阶段 2：审批决策回填后，重跑批准的命令并产出最终结果（不再暂停）。
        if tool_decisions:
            final_text = self._continue_after_approval(user_text, tool_decisions)
            for record in self._subtask_events():
                yield StreamMessage(
                    event=StreamEvents.MULTI_AGENT_SUBAGENT, data=record
                )
            yield from self._final_events(messages, user_text, final_text)
            return

        # 阶段 1：跑编排；收集到的待审批命令先回传用户，暂停等待决策。
        _, final_text, pending_approvals = self._run_orchestration(user_text)
        for record in self._subtask_events():
            yield StreamMessage(
                event=StreamEvents.MULTI_AGENT_SUBAGENT, data=record
            )

        if pending_approvals:
            self._approval_ctx = {
                "text": user_text,
                "pending_approvals": pending_approvals,
            }
            yield StreamMessage(
                event=StreamEvents.APPROVAL_REQUIRED,
                data={
                    "pending_approvals": pending_approvals,
                    "messages": messages,
                },
            )
            return

        yield from self._final_events(messages, user_text, final_text)

    def _continue_after_approval(
        self, user_text: str, tool_decisions: Dict[str, bool]
    ) -> str:
        """阶段 2：按用户审批决策重跑批准的命令，返回终局文本。

        批准（decision=True）→ 以 user_approved=True 重跑命令并更新子任务记录；
        拒绝 → 对应子任务记录为失败。随后重新归并 + 终局归纳。
        """
        ctx = getattr(self, "_approval_ctx", None)
        if ctx is None or self.orchestrator is None:
            # 无审批上下文（防御）：直接按原路径跑一遍编排。
            _, final_text, _ = self._run_orchestration(user_text)
            return final_text

        records = self.orchestrator.last_records
        executor = self.orchestrator.tool_executor
        if executor is None:
            return user_text

        def set_record(index: Optional[int], state: str, result: str) -> None:
            if index is not None and 0 <= index < len(records):
                records[index].state = state
                records[index].result = result

        for req in ctx["pending_approvals"]:
            index = req.get("index")
            tc_id = req.get("tool_call_id", "")
            decision = tool_decisions.get(tc_id)
            if decision is None:
                decision = tool_decisions.get(req.get("tool_name", ""))
            if not decision:
                set_record(index, "TASK_STATE_FAILED", "用户拒绝审批，命令未执行。")
                continue
            # 批准 → 以 user_approved=True 重跑命令
            try:
                invoke_context = ToolInvokeContext(
                    user_approved=True,
                    tool_name=req["tool_name"],
                    toolset=executor.get_toolset_for(req["tool_name"]),
                )
                res = executor.execute_tool(
                    req["tool_name"],
                    req.get("params") or {},
                    invoke_context,
                    tool_call_id=tc_id,
                )
                if res.result.status in (
                    StructuredToolResultStatus.SUCCESS,
                    StructuredToolResultStatus.NO_DATA,
                ):
                    content = (
                        res.result.data
                        if isinstance(res.result.data, str)
                        else json.dumps(
                            res.result.data, ensure_ascii=False, default=str
                        )
                    )
                    set_record(
                        index,
                        "TASK_STATE_COMPLETED",
                        f"命令执行成功（{res.tool_name}）:\n{content}",
                    )
                else:
                    set_record(
                        index,
                        "TASK_STATE_FAILED",
                        f"命令执行失败: {res.result.error or '未知错误'}",
                    )
            except Exception as exc:  # noqa: BLE001 - 审批后重跑失败如实记录
                logger.warning("审批后重跑命令失败: %s", exc)
                set_record(index, "TASK_STATE_FAILED", f"命令执行失败: {exc}")

        merged = merge_results(user_text, records)
        self.orchestrator.last_records = records
        return self._finalize(user_text, merged)

    def _final_events(
        self,
        messages: List[Dict[str, Any]],
        user_text: str,
        final_text: str,
    ) -> Generator[StreamMessage, None, None]:
        """产出多Agent 最终结果事件（DONE / ANSWER_DELTA / ANSWER_END）。"""
        yield StreamMessage(
            event=StreamEvents.MULTI_AGENT_DONE,
            data={"content": final_text, "task": user_text},
        )
        yield StreamMessage(
            event=StreamEvents.ANSWER_DELTA, data={"content": final_text}
        )
        # 与 ToolCallingLLM 一致：把 assistant 回复追加进消息，供 _run_turn
        # 更新 session_history —— 否则多Agent 模式下每轮上下文不累积（多轮失效）。
        yield StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={
                "content": final_text,
                "messages": list(messages) + [{"role": "assistant", "content": final_text}],
            },
        )

    def _subtask_events(self) -> List[Dict[str, Any]]:
        """把最近一次编排的子任务明细转成流事件数据（统一序列化，B1）。"""
        orchestrator = self.orchestrator
        if orchestrator is None:
            return []
        return subtask_dicts(orchestrator.last_records)

    @staticmethod
    def _last_user_text(messages: List[Dict[str, Any]]) -> str:
        """从 OpenAI 风格消息里取最后一个 user 消息的文本。"""
        for msg in reversed(messages):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            return message_text(msg.get("content") or "")
        return ""


__all__ = ["MAIN_SYSTEM_PROMPT", "MainAgent"]
