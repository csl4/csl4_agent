"""技能触发任务 + 历史查询流程集成测试（T028，FR-006/007/008）。

链路：Orchestrator/MainAgent 装配技能库与环境信息 → LLM 调用收到注入的
技能/环境上下文；SubAgent 命令执行落历史；会话记录可查询。
"""

from pathlib import Path

from agent.core.a2a.protocol import (
    TaskState,
    make_task,
    set_task_state,
)
from agent.core.agents.main_agent import MainAgent
from agent.core.agents.orchestrator import Orchestrator
from agent.core.agents.subagent import SubAgent
from agent.core.history.store import HistoryStore
from agent.core.skills.env_info import collect_env_info, format_env_info
from agent.core.skills.library import SkillLibrary
from agent.core.tool_executor import ToolExecutor
from tests.helpers import ScriptedLLM


def _write_skill(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "disk.yaml"
    path.write_text(
        "name: disk-analysis\n"
        "description: 分析磁盘与目录占用\n"
        "instructions: 使用 df 与 du 命令分析磁盘占用。\n"
        "tool_bindings: [bash]\n"
        "keywords: [磁盘, disk, df, du, 占用]\n",
        encoding="utf-8",
    )
    return path


class TestSkillInjection:
    def test_skill_injected_into_orchestrator_prompt(self, tmp_path: Path, bash_executor) -> None:
        """匹配的技能指令出现在编排 LLM 的系统提示中（FR-006）。"""
        skill_path = _write_skill(tmp_path / "skills")
        lib = SkillLibrary(directory=skill_path.parent)
        llm = ScriptedLLM([
            '[{"kind":"command","text":"df -h"}, {"kind":"command","text":"du -sh ."}]',
            "磁盘占用分析完成。",
        ])
        orchestrator = Orchestrator(
            agent_id="o", llm=llm, tool_executor=bash_executor,
            max_subagents=2, skill_library=lib,
        )
        main_agent = MainAgent(agent_id="m", orchestrator=orchestrator, llm=llm)

        task = make_task("t-skill")
        set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
        # 用户任务文本经 task_inputs 注册表流入编排层，触发技能自动匹配（FR-006）。
        main_agent.record_task_input(task.id, "请帮我分析磁盘占用情况")
        result = main_agent.run_task(task)

        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        assert llm.calls, "编排 LLM 未被调用"
        system_prompt = llm.calls[0][0]["content"]
        assert "disk-analysis" in system_prompt  # 技能注入
        assert "df" in system_prompt

    def test_env_info_injected_into_context(self, tmp_path: Path, bash_executor) -> None:
        """环境信息（工作目录等）注入 Agent 上下文（FR-007）。"""
        env = collect_env_info(tool_names=["bash"])
        knowledge = format_env_info(env)
        llm = ScriptedLLM([
            '[{"kind":"command","text":"echo hi"}]',
            "完成。",
        ])
        orchestrator = Orchestrator(
            agent_id="o", llm=llm, tool_executor=bash_executor,
            max_subagents=1, knowledge_text=knowledge,
        )
        main_agent = MainAgent(agent_id="m", orchestrator=orchestrator, llm=llm)

        task = make_task("t-env")
        set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
        main_agent.record_task_input(task.id, "请列出当前目录内容")
        result = main_agent.run_task(task)

        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        system_prompt = llm.calls[0][0]["content"]
        assert "当前环境信息" in system_prompt
        assert env.cwd in system_prompt


class TestHistoryFlow:
    def test_command_execution_recorded(self, tmp_path: Path, bash_executor) -> None:
        """SubAgent 命令执行落历史，可按命令模式查询（FR-008/T034）。"""
        store = HistoryStore(path=tmp_path / "history.jsonl")
        subagent = SubAgent(
            agent_id="sub-1", tool_executor=bash_executor, history=store
        )
        task = make_task("t-hist-cmd")
        set_task_state(task, TaskState.TASK_STATE_SUBMITTED)
        from agent.core.a2a.client import InProcessA2AClient

        client = InProcessA2AClient(subagent)
        result = client.send_task("echo history-flow-ok")

        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        found = store.query_command("history-flow-ok")
        assert len(found) == 1
        assert found[0].type == "command"
        assert "history-flow-ok" in found[0].payload["command"]

    def test_session_record_and_query(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.jsonl")
        store.append_session(session_id="s-flow-1", payload={"first_message": "hi"})
        found = store.query_session("s-flow-1")
        assert len(found) == 1
        assert found[0].type == "session"
        assert found[0].payload["first_message"] == "hi"
