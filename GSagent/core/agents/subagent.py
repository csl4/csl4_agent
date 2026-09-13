"""动态 SubAgent：执行子任务（FR-001 四角色之一）。

SubAgent 把子任务文本映射为一次 ToolExecutor.execute_tool 调用并收集结果：
- 结构化描述符 `{"tool": "...", "params": {...}}` → 精确指定工具与参数；
- 否则若文本以已知 shell 命令开头 → 经命令工具执行（带前缀审批语义）；
- 其余 → 视为无法执行，标记 FAILED。

命令执行（US2，FR-002/003/004）：
- 命令子任务优先走轻量沙箱工具（sandbox：审批层 + 子进程隔离 + 超时 +
  受控工作目录），未注册时回退 bash（US1 行为不变）；
- 执行前探测终端并把检测结果随参数传入，由沙箱按目标终端改写同义命令
  （FR-002）；
- 命令经审批/校验层（user_approved=False），未经白名单放行的命令会得到
  APPROVAL_REQUIRED，由编排层如实记录，绝不静默执行。
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from GSagent.core.a2a.protocol import Task, TaskState, set_task_state
from GSagent.core.agents.base_agent import AgentRole, BaseAgent, task_input_text
from GSagent.core.env.terminal import detect_shell, split_command_segments
from GSagent.core.history.store import HistoryStore
from GSagent.core.models import (
    StructuredToolResultStatus,
    ToolCallResult,
    ToolInvokeContext,
)
from GSagent.core.tools import ToolExecutor

logger = logging.getLogger(__name__)

# 常见的 shell 命令前缀（用于启发式判定「这是命令子任务」）。
# 与 bash toolset 的 CORE/EXTENDED 白名单命令对齐，保证子任务可被真实执行。
# 含命令包装/终端程序（powershell/cmd/bash/wsl）：LLM 拆解时常产出
# "powershell -Command ..." 这类包装命令，首词识别后即可交给命令工具执行。
SHELL_COMMAND_PREFIXES = frozenset(
    {
        "df", "du", "ls", "dir", "cat", "echo", "pwd", "whoami", "uname",
        "date", "grep", "head", "tail", "sort", "uniq", "wc", "cut", "tr",
        "id", "hostname", "which", "type", "git", "kubectl", "find", "stat",
        "base64", "mkdir", "rm", "touch", "cd", "python",
        "get-childitem", "get-content", "write-output",
        # 命令包装/终端程序（Windows / 跨终端）
        "powershell", "pwsh", "cmd", "bash", "wsl",
    }
)


def parse_subtask_descriptor(text: str) -> Optional[Dict[str, Any]]:
    """若文本是 JSON 结构化描述符则解析为 dict，否则返回 None。"""
    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def command_prefixes_for(command: str) -> List[str]:
    """从命令文本提取 suggested_prefixes（bash 工具要求，每段提供命令名前缀）。

    取每个命令段（按 |、&&、;、换行 分隔）的首词即可，与白名单的「命令名」粒度对齐。
    段切分复用 terminal.split_command_segments（命令段切分逻辑唯一化）。
    """
    prefixes: List[str] = []
    for seg in split_command_segments(command):
        words = seg.strip().split()
        if words:
            prefixes.append(words[0])
    return prefixes


class SubAgent(BaseAgent):
    """动态创建的 SubAgent：经 ToolExecutor 执行子任务。"""

    def __init__(
        self,
        agent_id: str,
        tool_executor: ToolExecutor,
        name: str = "",
        parent: Optional[BaseAgent] = None,
        history: Optional[HistoryStore] = None,
    ) -> None:
        super().__init__(agent_id, AgentRole.SUBAGENT, name=name, parent=parent)
        self.tool_executor = tool_executor
        # 审批回传：命令子任务需要审批时暂存审批请求，供 Orchestrator 收集、
        # MainAgent 回传用户审批（批准后阶段 2 重跑，不再静默跳过）。
        self.pending_approval: Optional[Dict[str, Any]] = None
        # US3 FR-008：命令执行历史库（可选，注入后每次命令执行落一条记录）。
        self.history = history

    def run_task(self, task: Task) -> Task:
        text = task_input_text(task, self)
        tool_name, params = self._resolve_execution(text)
        if tool_name is None:
            set_task_state(
                task,
                TaskState.TASK_STATE_FAILED,
                "无法把子任务解析为可执行的工具调用。",
            )
            return task

        self._record_command(tool_name, params, task)
        context = ToolInvokeContext(
            tool_name=tool_name,
            user_approved=False,
            toolset=self.tool_executor.get_toolset_for(tool_name),
        )
        result = self.tool_executor.execute_tool(
            tool_name, params, context, tool_call_id=f"sub-{task.id}"
        )
        return self._complete_from_result(task, result)

    def _record_command(
        self, tool_name: str, params: Dict[str, Any], task: Task
    ) -> None:
        """命令子任务执行前把命令写入历史（FR-008/T034）。

        仅记录命令类工具（sandbox/bash）且带 command 参数的调用。
        """
        if self.history is None:
            return
        command = params.get("command") if isinstance(params, dict) else None
        if not command:
            return
        self.history.append_command(
            command=str(command),
            session_id=task.id,
            agent_id=self.agent_id,
            shell=str(params.get("shell", "") or ""),
        )

    def _pick_command_tool(self) -> Optional[str]:
        """命令子任务优先走轻量沙箱工具；未注册时回退 bash（US1 行为不变）。"""
        for name in ("sandbox", "bash"):
            if self.tool_executor.get_tool_by_name(name) is not None:
                return name
        return None

    def _resolve_execution(self, text: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """把子任务文本解析为 (tool_name, params)；无法解析时返回 (None, {})。"""
        descriptor = parse_subtask_descriptor(text)
        if descriptor is not None and "tool" in descriptor:
            return str(descriptor["tool"]), dict(descriptor.get("params") or {})

        stripped = (text or "").strip()
        if stripped:
            first = stripped.split()[0].lower()
            if first in SHELL_COMMAND_PREFIXES:
                tool_name = self._pick_command_tool()
                if tool_name is None:
                    return None, {}
                # FR-002：探测终端并随参数传入，命令工具按目标终端改写执行。
                return tool_name, {
                    "command": stripped,
                    "suggested_prefixes": command_prefixes_for(stripped),
                    "shell": detect_shell().value,
                }
        return None, {}

    def _complete_from_result(self, task: Task, result: ToolCallResult) -> Task:
        status = result.result.status
        if status == StructuredToolResultStatus.SUCCESS:
            content = (
                result.result.data
                if isinstance(result.result.data, str)
                else json.dumps(result.result.data, ensure_ascii=False, default=str)
            )
            set_task_state(
                task,
                TaskState.TASK_STATE_COMPLETED,
                f"命令执行成功（{result.tool_name}）:\n{content}",
            )
        elif status == StructuredToolResultStatus.NO_DATA:
            set_task_state(
                task,
                TaskState.TASK_STATE_COMPLETED,
                f"命令执行成功（{result.tool_name}，无输出）。",
            )
        elif status == StructuredToolResultStatus.APPROVAL_REQUIRED:
            # 审批回传：把审批请求暂存到 pending_approval，由 Orchestrator 收集、
            # MainAgent 回传用户审批（批准后阶段 2 以 user_approved=True 重跑）。
            # task 先标 FAILED（A2A 需终止态），批准后阶段 2 更新对应记录。
            params = dict(result.result.params or {})
            self.pending_approval = {
                "tool_call_id": f"sub-{task.id}",
                "tool_name": result.tool_name,
                "params": params,
                "reason": result.result.error or "工具需要人工审批",
                "prefixes_to_save": list(params.get("suggested_prefixes") or []),
                "text": task_input_text(task, self),
                "index": None,  # Orchestrator 派发后按顺序填充
            }
            set_task_state(
                task,
                TaskState.TASK_STATE_FAILED,
                f"工具 '{result.tool_name}' 需要人工审批，等待用户决策。",
            )
        else:
            set_task_state(
                task,
                TaskState.TASK_STATE_FAILED,
                f"工具 '{result.tool_name}' 执行失败: "
                f"{result.result.error or '未知错误'}",
            )
        return task


__all__ = [
    "SHELL_COMMAND_PREFIXES",
    "SubAgent",
    "command_prefixes_for",
    "parse_subtask_descriptor",
]
