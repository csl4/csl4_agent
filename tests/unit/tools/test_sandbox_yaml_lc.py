"""sandbox + yaml 工具 langchain 化测试（T019/T020）。

验证：
- sandbox @tool 复用 validate_command 动态审批（白名单外 → APPROVAL_REQUIRED）
- yaml 工具渲染（shlex.quote 防注入 + Jinja2 模板）
"""

from langchain_core.messages import AIMessage

from GSagent.core.agents.tool_calling_llm import ToolCallingLLM
from GSagent.core.tools.registry import ToolRegistry
from GSagent.plugins.toolsets.sandbox.lc_tools import create_sandbox_tools
from GSagent.plugins.toolsets.yaml_lc_loader import load_yaml_toolsets_lc
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


class TestSandboxLc:
    def test_approval_required_triggers_interrupt(self):
        """白名单外命令 → APPROVAL_REQUIRED（动态审批复用 validate_command）。"""
        reg = ToolRegistry()
        sb = create_sandbox_tools({}, get_approved_prefixes=lambda: reg.approved_prefixes)[0]
        reg.register(sb)
        llm = FakeChatLLM(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "sandbox",
                            "args": {"command": "custom_cmd --flag", "suggested_prefixes": ["custom_cmd"]},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                    response_metadata=_usage(),
                ),
                AIMessage(content="done", response_metadata=_usage()),
            ]
        )
        agent = ToolCallingLLM(chat_model=llm, tools_registry=reg, max_steps=5, enable_compaction=False)
        events = list(agent.call_stream(messages=[{"role": "user", "content": "run"}]))
        assert any(e.event == StreamEvents.APPROVAL_REQUIRED for e in events)


class TestYamlLcLoader:
    def test_load_and_render(self, tmp_path):
        """YAML 工具加载为 StructuredTool 并渲染（shlex.quote 防注入）。"""
        (tmp_path / "tools.yaml").write_text(
            """
name: test_toolset
tools:
  - name: my_echo
    description: echo a value
    parameters:
      value:
        type: string
        required: true
    command: "echo {{ value }}"
""",
            encoding="utf-8",
        )
        tools = load_yaml_toolsets_lc(tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "my_echo"
        result = tools[0].invoke({"value": "hello world"})
        assert "echo 'hello world'" in result  # shlex.quote 转义
