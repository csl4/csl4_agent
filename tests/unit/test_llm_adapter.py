"""llm_adapter 转换层测试（T005，US1）。

验证（contracts/messages.md §2，SC-005）：
- dict_to_messages / messages_to_dict 双向保真（含 tool_call_id / tool_calls / name）
- extract_usage 从 AIMessage.response_metadata["token_usage"] 提取正确
"""

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from GSagent.core.llm_adapter import (
    dict_to_messages,
    extract_usage,
    messages_to_dict,
)


class TestDictMessagesRoundtrip:
    def test_roundtrip_preserves_fields(self):
        """OpenAI dict → BaseMessage → OpenAI dict 双向保真。"""
        dicts = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "content": "result", "tool_call_id": "c1", "name": "echo"},
        ]
        msgs = dict_to_messages(dicts)
        assert [type(m).__name__ for m in msgs] == [
            "SystemMessage",
            "HumanMessage",
            "AIMessage",
            "ToolMessage",
        ]
        back = messages_to_dict(msgs)
        assert len(back) == 4
        # assistant tool_calls 保真
        assert back[2]["tool_calls"][0]["function"]["name"] == "echo"
        assert back[2]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
        # tool 消息 tool_call_id/name 保真
        assert back[3]["tool_call_id"] == "c1"
        assert back[3]["name"] == "echo"

    def test_langchain_tool_calls_format(self):
        """langchain 格式 tool_calls（name/args）正确互转。"""
        msgs = dict_to_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "echo", "arguments": '{"x": 2}'},
                        }
                    ],
                }
            ]
        )
        ai = msgs[0]
        assert isinstance(ai, AIMessage)
        assert ai.tool_calls[0]["name"] == "echo"
        assert ai.tool_calls[0]["args"] == {"x": 2}


class TestExtractUsage:
    def test_extracts_token_usage(self):
        """从 response_metadata.token_usage 提取用量。"""
        ai = AIMessage(
            content="x",
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                }
            },
        )
        usage = extract_usage(ai)
        assert usage.prompt_tokens == 5
        assert usage.completion_tokens == 3
        assert usage.total_tokens == 8

    def test_missing_usage_defaults_zero(self):
        """无 token_usage → 全 0（不抛错）。"""
        usage = extract_usage(AIMessage(content="x"))
        assert usage.total_tokens == 0
