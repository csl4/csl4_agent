"""沙箱执行 + 高风险命令确认/拦截集成测试（T021，FR-003/004）。

轻量沙箱 toolset（subprocess 隔离 + 超时 + 受控工作目录）：
- 低风险命令（白名单命中）→ 沙箱直跑；
- 高风险命令（未命中白名单）→ 要求审批（APPROVAL_REQUIRED）；
- 敏感路径访问 → 绝对拒绝（不可被审批豁免）；
- 沙箱类型为 container（v1 未实现）→ 明确错误而非静默失败（FR-004）；
- 超时 → 明确超时错误；受控工作目录生效。

shell 显式传 bash，用真实 Git Bash 子进程执行（跨平台确定性）。
"""

import pytest

from agent.core.a2a.protocol import TaskState, make_task, set_task_state
from agent.core.agents.orchestrator import Orchestrator
from agent.core.models import StructuredToolResultStatus, ToolInvokeContext
from agent.core.tool_executor import ToolExecutor
from agent.core.tools import ToolsetTag
from agent.plugins.toolsets.sandbox.sandbox_toolset import create_sandbox_toolset
from tests.helpers import ScriptedLLM


@pytest.fixture
def sandbox_executor() -> ToolExecutor:
    """带 extended 白名单的轻量沙箱执行器。"""
    toolset = create_sandbox_toolset(
        {"builtin_allowlist": "extended", "timeout_seconds": 10}
    )
    return ToolExecutor(toolsets=[toolset], toolset_tag_filter=[ToolsetTag.CLI])


def _ctx(executor: ToolExecutor, user_approved: bool = False) -> ToolInvokeContext:
    return ToolInvokeContext(
        tool_name="sandbox",
        user_approved=user_approved,
        toolset=executor.get_toolset_for("sandbox"),
    )


def test_sandbox_low_risk_command(sandbox_executor: ToolExecutor) -> None:
    """白名单命令（echo）经沙箱执行成功。"""
    result = sandbox_executor.execute_tool(
        "sandbox",
        {"command": "echo sandbox-ok", "suggested_prefixes": ["echo"], "shell": "bash"},
        _ctx(sandbox_executor),
        "call-1",
    )
    assert result.result.status == StructuredToolResultStatus.SUCCESS
    assert "sandbox-ok" in result.result.data


def test_sandbox_high_risk_requires_approval(sandbox_executor: ToolExecutor) -> None:
    """高风险命令（rm，未命中白名单）→ 执行前要求审批（FR-003）。"""
    result = sandbox_executor.execute_tool(
        "sandbox",
        {
            "command": "rm -rf /tmp/danger-dir",
            "suggested_prefixes": ["rm"],
            "shell": "bash",
        },
        _ctx(sandbox_executor),
        "call-2",
    )
    assert result.result.status == StructuredToolResultStatus.APPROVAL_REQUIRED


def test_sandbox_sensitive_path_denied(sandbox_executor: ToolExecutor) -> None:
    """敏感路径访问（~/.ssh 凭据）→ 绝对拒绝，审批也无法豁免。"""
    result = sandbox_executor.execute_tool(
        "sandbox",
        {"command": "cat ~/.ssh/id_rsa", "suggested_prefixes": ["cat"], "shell": "bash"},
        _ctx(sandbox_executor),
        "call-3",
    )
    assert result.result.status == StructuredToolResultStatus.ERROR
    assert "sensitive" in (result.result.error or "").lower()


def test_sandbox_container_type_clear_error() -> None:
    """沙箱类型 container（v1 未实现）→ 明确错误，不静默（FR-004）。"""
    toolset = create_sandbox_toolset(
        {"type": "container", "builtin_allowlist": "extended"}
    )
    executor = ToolExecutor(toolsets=[toolset])
    result = executor.execute_tool(
        "sandbox",
        {"command": "echo hi", "suggested_prefixes": ["echo"]},
        _ctx(executor, user_approved=True),
        "call-4",
    )
    assert result.result.status == StructuredToolResultStatus.ERROR
    assert "container" in (result.result.error or "").lower()


def test_sandbox_timeout(sandbox_executor: ToolExecutor) -> None:
    """超时（sleep 5 / timeout=1）→ 明确超时错误。"""
    result = sandbox_executor.execute_tool(
        "sandbox",
        {
            "command": "sleep 5",
            "suggested_prefixes": ["sleep"],
            "shell": "bash",
            "timeout": 1,
        },
        _ctx(sandbox_executor, user_approved=True),
        "call-5",
    )
    assert result.result.status == StructuredToolResultStatus.ERROR
    assert "timed out" in (result.result.error or "").lower()


def test_sandbox_working_dir(sandbox_executor: ToolExecutor, tmp_path) -> None:
    """受控工作目录生效（ls 列出指定目录内容而非当前目录）。"""
    marker = tmp_path / "sandbox-marker.txt"
    marker.write_text("x", encoding="utf-8")
    result = sandbox_executor.execute_tool(
        "sandbox",
        {
            "command": "ls",
            "suggested_prefixes": ["ls"],
            "shell": "bash",
            "cwd": str(tmp_path),
        },
        _ctx(sandbox_executor),
        "call-6",
    )
    assert result.result.status == StructuredToolResultStatus.SUCCESS
    assert "sandbox-marker.txt" in result.result.data


def test_subagent_command_runs_via_sandbox(sandbox_executor: ToolExecutor) -> None:
    """T024/T026 接线：命令子任务经 SubAgent 走沙箱执行（带审批层）。"""
    llm = ScriptedLLM([
        '[{"kind":"command","text":"echo multi-agent-sandbox-ok"}]',
        "执行完成。",
    ])
    orchestrator = Orchestrator(
        agent_id="o", llm=llm, tool_executor=sandbox_executor, max_subagents=1
    )
    task = make_task("t-sandbox-subagent")
    set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
    result = orchestrator.run_task(task)

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    records = orchestrator.last_records
    assert records and records[0].state == "TASK_STATE_COMPLETED"
    assert "multi-agent-sandbox-ok" in records[0].result
