"""动态 SubAgent：执行子任务（FR-001 四角色之一）。

SubAgent 把子任务文本映射为一次 ToolExecutor.execute_tool 调用并收集结果：
- 结构化描述符 `{"tool": "...", "params": {...}}` → 精确指定工具与参数；
- 否则若文本以已知 shell 命令开头 → 经 bash 工具执行（带前缀审批语义）；
- 其余 → 视为无法执行，标记 FAILED。

命令经现有 bash toolset 的审批/校验层（user_approved=False），因此
未经白名单放行的命令会得到 APPROVAL_REQUIRED，由编排层如实记录，
绝不静默执行（与 US2 安全防护一致）。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agent.core.a2a.protocol import Task, TaskState, set_task_state
from agent.core.agents.base_agent import AgentRole, BaseAgent, task_input_text
from agent.core.models import (
    StructuredToolResultStatus,
    ToolCallResult,
    ToolInvokeContext,
)
from agent.core.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# 常见的 shell 命令前缀（用于启发式判定「这是命令子任务」）。
# 与 bash toolset 的 CORE/EXTENDED 白名单命令对齐，保证子任务可被真实执行。
SHELL_COMMAND_PREFIXES = frozenset(
    {
        "df", "du", "ls", "dir", "cat", "echo", "pwd", "whoami", "uname",
        "date", "grep", "head", "tail", "sort", "uniq", "wc", "cut", "tr",
        "id", "hostname", "which", "type", "git", "kubectl", "find", "stat",
        "base64", "mkdir", "rm", "touch", "cd", "python",
        "get-childitem", "get-content", "write-output",
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

    取每个命令段（按 |、&&、; 分隔）的首词即可，与白名单的「命令名」粒度对齐。
    """
    segments = re.split(r"\|\||&&|[|;&]", command or "")
    prefixes: List[str] = []
    for seg in segments:
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
    ) -> None:
        super().__init__(agent_id, AgentRole.SUBAGENT, name=name, parent=parent)
        self.tool_executor = tool_executor

    def run_task(self, task: Task) -> Task:
        text = task_input_text(task, self.context_messages())
        tool_name, params = self._resolve_execution(text)
        if tool_name is None:
            set_task_state(
                task,
                TaskState.TASK_STATE_FAILED,
                "无法把子任务解析为可执行的工具调用。",
            )
            return task

        context = ToolInvokeContext(
            tool_name=tool_name,
            user_approved=False,
            toolset=self.tool_executor.get_toolset_for(tool_name),
        )
        result = self.tool_executor.execute_tool(
            tool_name, params, context, tool_call_id=f"sub-{task.id}"
        )
        return self._complete_from_result(task, result)

    def _resolve_execution(self, text: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """把子任务文本解析为 (tool_name, params)；无法解析时返回 (None, {})。"""
        descriptor = parse_subtask_descriptor(text)
        if descriptor is not None and "tool" in descriptor:
            return str(descriptor["tool"]), dict(descriptor.get("params") or {})

        stripped = (text or "").strip()
        if stripped:
            first = stripped.split()[0].lower()
            if first in SHELL_COMMAND_PREFIXES:
                return "bash", {
                    "command": stripped,
                    "suggested_prefixes": command_prefixes_for(stripped),
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
            set_task_state(
                task,
                TaskState.TASK_STATE_FAILED,
                f"工具 '{result.tool_name}' 需要人工审批，SubAgent 未获批准，已跳过。",
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
