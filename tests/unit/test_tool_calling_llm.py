"""ToolCallingLLM 与 BaseAgent 继承体系合并的单元测试。

背景：ToolCallingLLM 原为独立类，现改为继承 BaseAgent（AgentRole.MAIN），
复用基类身份（agent_id/role/name/context），并以 run_task 作为 A2A 无头入口。
"""

from agent.core.a2a.protocol import TaskState, make_task, set_task_state
from agent.core.agents import AgentRole, BaseAgent
from agent.core.agents.base_agent import task_result_text
from agent.core.tool_calling_llm import ToolCallingLLM
from agent.core.tool_executor import ToolExecutor
from tests.helpers import ScriptedLLM


def test_extends_base_agent() -> None:
    """ToolCallingLLM 是 BaseAgent 子类，携带 MAIN 角色身份。"""
    agent = ToolCallingLLM(
        tool_executor=ToolExecutor(toolsets=[]),
        llm=ScriptedLLM([]),
    )
    assert isinstance(agent, BaseAgent)
    assert agent.role == AgentRole.MAIN
    assert agent.agent_id == "main"
    assert agent.name == "main"
    # 基类身份字段可用
    assert agent.context_messages() == []


def test_run_task_completes_via_main_loop() -> None:
    """run_task（A2A 无头入口）消费 call_stream 主循环并完成 Task。"""
    llm = ScriptedLLM(["hello answer"])
    agent = ToolCallingLLM(
        tool_executor=ToolExecutor(toolsets=[]),
        llm=llm,
    )
    task = make_task("t-run")
    set_task_state(task, TaskState.TASK_STATE_SUBMITTED)

    result = agent.run_task(task)

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    assert task_result_text(result) == "hello answer"
