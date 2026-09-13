"""bash @tool 动态审批测试（T016）。

验证（contracts/tools.md，SC-003）：
- validate_command DENIED（sudo 硬编码块）→ ERROR 事件，不执行
- APPROVAL_REQUIRED 命令 → 审批 interrupt 事件；批准后 record_approval 前缀
- CommandGuard 仍拦截破坏性命令（守卫层）
"""

from langchain_core.messages import AIMessage

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.policy.command_guard import CommandGuard
from GSagent.core.tools.registry import ToolRegistry
from GSagent.plugins.toolsets.bash.lc_tools import create_bash_tools
from GSagent.utils.stream import StreamEvents
from tests.helpers import FakeChatLLM


def _usage(p=5, c=3):
    return {
        "token_usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    }


def _tc(command, prefixes, tc_id="c1"):
    return {
        "name": "bash",
        "args": {"command": command, "suggested_prefixes": prefixes},
        "id": tc_id,
        "type": "tool_call",
    }


def _registry():
    reg = ToolRegistry().configure(command_guard=CommandGuard())
    bash_tool = create_bash_tools({}, get_approved_prefixes=lambda: reg.approved_prefixes)[0]
    reg.register(bash_tool)
    return reg


class TestBashLcTools:
    def test_denied_command_emits_error(self):
        """validate_command DENIED（sudo 硬编码块）→ ERROR 事件，无最终答案。"""
        reg = _registry()
        llm = FakeChatLLM(
            responses=[AIMessage(content="", tool_calls=[_tc("sudo ls /root", ["sudo"])], response_metadata=_usage())]
        )
        agent = ToolCallingLLM(chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False)
        events = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        assert any(e.event == StreamEvents.ERROR for e in events), "DENIED 应产 ERROR"
        assert not any(e.event == StreamEvents.APPROVAL_REQUIRED for e in events)

    def test_approval_required_triggers_interrupt(self):
        """白名单外命令 → APPROVAL_REQUIRED 事件（动态审批）。"""
        reg = _registry()
        llm = FakeChatLLM(
            responses=[
                AIMessage(content="", tool_calls=[_tc("custom_cmd --flag", ["custom_cmd"])], response_metadata=_usage()),
                AIMessage(content="approved done", response_metadata=_usage()),
            ]
        )
        agent = ToolCallingLLM(chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False)
        events1 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        approval = [e for e in events1 if e.event == StreamEvents.APPROVAL_REQUIRED]
        assert approval, "应有 APPROVAL_REQUIRED"
        assert "custom_cmd" in approval[0].data["params"]["command"]

        # 批准后：前缀记入 registry，断点续跑
        events2 = list(agent.call_stream(messages=[{"role": "user", "content": "run"}], tool_decisions={"c1": True}))
        end = [e for e in events2 if e.event == StreamEvents.ANSWER_END]
        assert end, "批准后应有最终答案"
        assert "custom_cmd" in reg.approved_prefixes, "批准前缀应记录"

    def test_command_guard_still_blocks(self):
        """CommandGuard 仍拦截破坏性命令（守卫层，先于 validate）。"""
        reg = _registry()
        llm = FakeChatLLM(
            responses=[AIMessage(content="", tool_calls=[_tc("rm -rf /etc", ["rm"])], response_metadata=_usage())]
        )
        agent = ToolCallingLLM(chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False)
        events = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        # CommandGuard 拦截 → 工具返回 blocked 错误 → 无 APPROVAL/ANSWER
        assert not any(e.event == StreamEvents.APPROVAL_REQUIRED for e in events)
        assert not any(e.event == StreamEvents.ANSWER_END for e in events)
