"""编排 Agent：任务拆解、并行调度、SubAgent 生命周期（FR-001 四角色之一）。

流程：
  1. 从任务文本拆解子任务（有 LLM 走 LLM 计划，否则走确定性启发式）；
  2. 为每个子任务创建 worker（命令→SubAgent，业务→BusinessAgent）；
  3. 经 InProcessA2AClient 并行派发（受 max_subagents 上限约束）；
  4. 归并结果 → 返回 COMPLETED Task（text=最终归纳；data Part 含子任务明细，
     供 MainAgent/CLI 生成结构化事件）。

设计要点：
- 单个子任务失败不中断整体（run_task_safe 兜底），由归并结果如实呈现。
- `last_records` 保存最近一次调度的子任务明细，供上层读取并生成事件。
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from GSagent.core.a2a.client import A2AClientError, InProcessA2AClient
from GSagent.core.a2a.protocol import (
    Task,
    TaskState,
    make_data_part,
    set_task_state,
)
from GSagent.core.agents.base_agent import (
    AgentRole,
    BaseAgent,
    SubtaskResult,
    subtask_dicts,
    task_input_text,
    task_state_text,
)
from GSagent.core.agents.business_agent import BusinessAgent
from GSagent.core.agents.subagent import SHELL_COMMAND_PREFIXES, SubAgent
from GSagent.core.history.store import HistoryStore
from GSagent.core.providers import LLM
from GSagent.core.skills.library import SkillLibrary, format_skills_block
from GSagent.core.tools import ToolExecutor

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = (
    "你是多Agent 系统中的编排 Agent。请把用户任务拆解为若干可并行子任务。"
    "只输出 JSON 数组，每个元素形如 "
    '{"kind": "command"|"business", "text": "子任务内容"}。'
    "命令类子任务(kind=command)的 text 必须是操作系统可直接执行的命令字符串"
    "（如 ls -la、Get-ChildItem -Path ...），绝不能是描述性伪命令（如"
    "\"list_directory ...\" 这种只是动作描述、不是真命令的文本）；命令要带完整"
    "参数，直接给目标命令本身，不要用 `powershell -Command ...` / `bash -c ...`"
    "之类的外壳包装。"
    "业务/分析类子任务(kind=business)的内容是待分析的领域问题。"
    "不要输出任何其他文字。"
)

# 拆解文本时的命令分隔符（与 bash 工具支持的连接符一致）。
_SPLIT_RE = re.compile(r"\s*(?:\&\&|;|\n)\s*")


def merge_results(task_text: str, records: List[SubtaskResult]) -> str:
    """把子任务执行记录归并为结构化自然语言回复（FR-005 / SC-006，T019）。"""
    if not records:
        return "未拆解出可执行子任务。"
    lines = [
        f"原始任务: {task_text or '(空)'}",
        f"共 {len(records)} 个子任务:",
        "",
    ]
    for rec in records:
        lines.append(f"  [{rec.state}] ({rec.kind}) {rec.text}")
        result = (rec.result or "").strip()
        if result:
            lines.append(f"      → {result}")
    failed = [r for r in records if r.state != "TASK_STATE_COMPLETED"]
    lines.append("")
    lines.append(
        "整体结论: "
        + ("全部子任务成功完成。" if not failed else f"{len(failed)} 个子任务未成功，详见上方。")
    )
    return "\n".join(lines)


class Orchestrator(BaseAgent):
    """编排 Agent：拆解 + 并行调度 + 结果归并。"""

    def __init__(
        self,
        agent_id: str = "orchestrator",
        llm: Optional[LLM] = None,
        tool_executor: Optional[ToolExecutor] = None,
        max_subagents: int = 4,
        worker_factory: Optional[Callable[[str, str], BaseAgent]] = None,
        name: str = "",
        parent: Optional[BaseAgent] = None,
        skill_library: Optional[SkillLibrary] = None,
        knowledge_text: str = "",
        history: Optional[HistoryStore] = None,
    ) -> None:
        super().__init__(
            agent_id,
            AgentRole.ORCHESTRATOR,
            name=name or "orchestrator",
            parent=parent,
            knowledge_text=knowledge_text,
        )
        self.llm = llm
        self.tool_executor = tool_executor
        self.max_subagents = max_subagents
        # worker_factory(kind, agent_id) -> BaseAgent；默认按 kind 造 SubAgent/BusinessAgent。
        self.worker_factory = worker_factory or self._default_worker
        # US3 FR-006/007：技能库与环境知识，注入编排 LLM 系统提示。
        self.skill_library = skill_library
        # US3 FR-008：会话/命令历史库，传递给命令 SubAgent 落执行记录。
        self.history = history
        self.last_records: List[SubtaskResult] = []
        # 审批回传：最近一次编排中需要人工审批的命令子任务请求（供 MainAgent 回传）。
        self.last_pending_approvals: List[Dict[str, Any]] = []

    # ---- worker 工厂 ----
    def _default_worker(self, kind: str, agent_id: str) -> BaseAgent:
        if kind == "command":
            if self.tool_executor is None:
                raise RuntimeError("Orchestrator 缺 tool_executor，无法创建命令 SubAgent。")
            return SubAgent(
                agent_id=agent_id,
                tool_executor=self.tool_executor,
                name=f"sub-{agent_id}",
                parent=self,
                history=self.history,
            )
        return BusinessAgent(
            agent_id=agent_id,
            llm=self.llm,
            name=f"biz-{agent_id}",
            parent=self,
            knowledge_text=self.knowledge_text,
        )

    # ---- 拆解 ----
    def decompose(self, text: str) -> List[Dict[str, str]]:
        """把任务文本拆解为子任务列表 [{kind, text}]。"""
        if self.llm is not None:
            plan = self._plan_with_llm(text)
            if plan:
                return plan
        return self._decompose_heuristic(text)

    def _build_system_prompt(self, text: str) -> str:
        """编排系统提示 + 命中的技能（FR-006）+ 环境知识（FR-007）。"""
        system = ORCHESTRATOR_SYSTEM_PROMPT
        if self.skill_library is not None:
            matched = self.skill_library.match(text)
            block = format_skills_block(matched)
            if block:
                system += "\n\n" + block
        if self.knowledge_text:
            system += "\n\n" + self.knowledge_text
        return system

    def _plan_with_llm(self, text: str) -> List[Dict[str, str]]:
        try:
            response = self.llm.completion(
                [
                    {"role": "system", "content": self._build_system_prompt(text)},
                    {"role": "user", "content": text or "(空任务)"},
                ]
            )
            content = (response.content or "").strip()
            start, end = content.find("["), content.rfind("]")
            if start == -1 or end == -1:
                return []
            data = json.loads(content[start : end + 1])
            return [
                {
                    "kind": (
                        "command"
                        if str(item.get("kind", "business")) == "command"
                        else "business"
                    ),
                    "text": str(item.get("text", "")),
                }
                for item in data
                if isinstance(item, dict) and item.get("text")
            ]
        except Exception as exc:  # noqa: BLE001 - 降级到启发式拆解
            logger.warning("Orchestrator LLM 拆解失败，回退启发式: %s", exc)
            return []

    def _decompose_heuristic(self, text: str) -> List[Dict[str, str]]:
        """确定性拆解：按 && / ; / 换行 切分命令；其余文本归为业务子任务。"""
        subtasks: List[Dict[str, str]] = []
        for part in _SPLIT_RE.split(text or ""):
            stripped = part.strip()
            if not stripped:
                continue
            first = stripped.split()[0].lower()
            if first in SHELL_COMMAND_PREFIXES:
                subtasks.append({"kind": "command", "text": stripped})
            else:
                subtasks.append({"kind": "business", "text": stripped})
        return subtasks

    # ---- 主入口 ----
    def run_task(self, task: Task) -> Task:
        text = task_input_text(task, self)
        subtasks = self.decompose(text)
        if not subtasks:
            set_task_state(task, TaskState.TASK_STATE_FAILED, "任务拆解为空，无法编排。")
            self.last_records = []
            return task

        records, pending_approvals = self._dispatch(subtasks, task.id)
        self.last_records = records
        self.last_pending_approvals = pending_approvals
        set_task_state(task, TaskState.TASK_STATE_COMPLETED, merge_results(text, records))
        self._attach_subtasks(task, records)
        return task

    def _dispatch(
        self, subtasks: List[Dict[str, str]], parent_id: str
    ) -> Tuple[List[SubtaskResult], List[Dict[str, Any]]]:
        """并行派发子任务；单个失败不中断整体。

        返回 ``(records, pending_approvals)``：pending_approvals 是需要人工
        审批的命令子任务请求（SubAgent 暂存于 ``worker.pending_approval``，
        供 MainAgent 回传用户审批后重跑，而非静默跳过）。
        """
        results: List[Optional[SubtaskResult]] = [None] * len(subtasks)
        pending_approvals: List[Dict[str, Any]] = []

        def run_one(index: int) -> SubtaskResult:
            sub = subtasks[index]
            worker = self.worker_factory(sub["kind"], f"{parent_id}-sub-{index}")
            client = InProcessA2AClient(worker)
            try:
                sub_task = client.send_task(
                    sub["text"], task_id=f"{parent_id}-sub-{index}"
                )
                return SubtaskResult(
                    index=index,
                    kind=sub["kind"],
                    text=sub["text"],
                    worker=worker.name,
                    state=TaskState.Name(sub_task.status.state),
                    result=task_state_text(sub_task),
                )
            except A2AClientError as exc:
                # 审批回传：命令子任务需要审批时，worker 已暂存审批请求。
                pending = getattr(worker, "pending_approval", None)
                if pending is not None:
                    pending["index"] = index
                    pending_approvals.append(pending)
                    return SubtaskResult(
                        index=index,
                        kind=sub["kind"],
                        text=sub["text"],
                        worker=worker.name,
                        state="TASK_STATE_FAILED",
                        result="需要人工审批，等待用户决策。",
                    )
                logger.warning("子任务 %s 失败: %s", sub["text"], exc)
                return SubtaskResult(
                    index=index,
                    kind=sub["kind"],
                    text=sub["text"],
                    worker=worker.name,
                    state="TASK_STATE_FAILED",
                    result=str(exc),
                )

        max_workers = max(1, min(self.max_subagents, len(subtasks)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(run_one, i) for i in range(len(subtasks))]
            for fut in as_completed(futures):
                rec = fut.result()
                results[rec.index] = rec
        return [r for r in results if r is not None], pending_approvals

    def _attach_subtasks(self, task: Task, records: List[SubtaskResult]) -> None:
        """把子任务明细以 data Part 附加到完成消息（供上层生成事件）。"""
        if not task.status.HasField("message"):
            return
        task.status.message.parts.append(
            make_data_part({"subtasks": subtask_dicts(records)})
        )


__all__ = [
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "Orchestrator",
    "merge_results",
]
