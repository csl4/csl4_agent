"""端到端多Agent 混合任务集成测试（US1 MVP，T011）。

链路：MainAgent → Orchestrator（拆解/并行调度）→ SubAgent（真实 bash
toolset 执行），主/编排/业务/SubAgent 四角色协作完成混合任务。

LLM 说明：以脚本化 FakeLLM 在 LLM 抽象层打桩（本机离线；且 litellm 走
httpx 传输、`responses` 无法拦截其请求 —— 因此不适用 responses 的 HTTP
mock，而用依赖注入桩，符合宪法「HTTP mock 用 responses，不用
@patch('requests.get')」的离线原则）。

真实 bash toolset：builtin_allowlist=extended，使 df/du 白名单直跑、
绕开审批交互（审批交互属 US2 范围）。
"""

import pytest

from agent.core.a2a.protocol import TaskState, make_task, set_task_state
from agent.core.agents.main_agent import MainAgent
from agent.core.agents.orchestrator import Orchestrator
from agent.core.llm import LLM, ModelResponse
from agent.core.models import ContextWindowUsage
from agent.core.tool_executor import ToolExecutor
from agent.core.tools import ToolsetTag
from agent.plugins.toolsets.bash.bash_toolset import create_bash_toolset


class ScriptedLLM(LLM):
    """按调用顺序返回预置回复的假 LLM（离线确定性测试）。"""

    def __init__(self, responses: list) -> None:
        super().__init__(model="fake-model")
        self.responses = list(responses)
        self.calls: list = []

    def completion(self, messages, tools=None, tool_choice="auto", temperature=0.7,
                   stream=False, response_format=None, drop_params=True) -> ModelResponse:
        self.calls.append(messages)
        if self.responses:
            return ModelResponse(content=self.responses.pop(0))
        return ModelResponse(content="(fallback)")

    def count_tokens(self, messages, tools=None) -> ContextWindowUsage:
        return ContextWindowUsage(total_tokens=1)

    def get_context_window_size(self) -> int:
        return 128000

    def get_maximum_output_token(self) -> int:
        return 4096


@pytest.fixture
def bash_executor() -> ToolExecutor:
    """带 extended 白名单的真实 bash toolset（df/du 免审批直跑）。"""
    toolset = create_bash_toolset({"builtin_allowlist": "extended"})
    return ToolExecutor(
        toolsets=[toolset],
        toolset_tag_filter=[ToolsetTag.CLI],
    )


def test_mixed_command_task_coordinates_roles(bash_executor: ToolExecutor) -> None:
    """混合任务（≥2 条命令）→ 自动拆解、并行执行、结构化归并。"""
    llm = ScriptedLLM([
        # ① 编排 Agent 拆解计划（LLM 路径）
        '[{"kind":"command","text":"df -h"}, {"kind":"command","text":"du -sh ."}]',
        # ② 主 Agent 终局归纳
        "磁盘占用分析完成。",
    ])
    orchestrator = Orchestrator(
        agent_id="orchestrator", llm=llm, tool_executor=bash_executor, max_subagents=2
    )
    main_agent = MainAgent(agent_id="main", orchestrator=orchestrator, llm=llm)

    task = make_task("t-integ-mixed")
    set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
    result = main_agent.run_task(task)

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    assert "磁盘占用分析完成" in result.status.message.parts[0].text

    # 两个命令子任务都真实执行成功（bash toolset 输出随命令文本回显）
    records = orchestrator.last_records
    assert len(records) == 2
    assert all(r["state"] == "TASK_STATE_COMPLETED" for r in records)
    joined = "\n".join(r["result"] for r in records)
    assert "df -h" in joined
    assert "du -sh ." in joined


def test_single_command_via_subagent(bash_executor: ToolExecutor) -> None:
    """单命令任务经 SubAgent 用真实 bash toolset 执行。"""
    llm = ScriptedLLM([
        '[{"kind":"command","text":"echo multi-agent-ok"}]',
        "执行完成。",
    ])
    orchestrator = Orchestrator(
        agent_id="o", llm=llm, tool_executor=bash_executor, max_subagents=2
    )
    main_agent = MainAgent(agent_id="m", orchestrator=orchestrator, llm=llm)

    task = make_task("t-integ-echo")
    set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
    result = main_agent.run_task(task)

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    records = orchestrator.last_records
    assert records and records[0]["state"] == "TASK_STATE_COMPLETED"
    assert "multi-agent-ok" in records[0]["result"]


def test_business_subtask_without_tool(bash_executor: ToolExecutor) -> None:
    """业务子任务不占用 bash 工具，走 BusinessAgent（LLM 或确定性兜底）。"""
    llm = ScriptedLLM([
        # 编排拆解：1 条业务子任务
        '[{"kind":"business","text":"分析本周磁盘增长趋势"}]',
        # 业务 Agent 回答
        "本周磁盘增长约 5%。",
        # 主 Agent 终局归纳
        "磁盘增长趋势已分析。",
    ])
    orchestrator = Orchestrator(
        agent_id="o", llm=llm, tool_executor=bash_executor, max_subagents=2
    )
    main_agent = MainAgent(agent_id="m", orchestrator=orchestrator, llm=llm)

    task = make_task("t-integ-biz")
    set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
    result = main_agent.run_task(task)

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    records = orchestrator.last_records
    assert len(records) == 1
    assert records[0]["kind"] == "business"
    assert records[0]["state"] == "TASK_STATE_COMPLETED"
    assert "本周磁盘增长约 5%" in records[0]["result"]
