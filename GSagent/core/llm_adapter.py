"""langchain BaseMessage 转换层（002-langchain-ecosystem，contracts/messages.md §2）。

编排内部流转 langchain ``BaseMessage``；既有外部消费方（StreamMessage 事件快照、
审计、截断）按 OpenAI dict 工作——本模块是**唯一转换点**：
- ``dict_to_messages``：OpenAI dict 列表 → ``BaseMessage`` 列表（外部输入兼容）。
- ``messages_to_dict``：``BaseMessage`` 列表 → OpenAI dict 列表（事件快照/既有消费方）。
- ``extract_usage``：``AIMessage.response_metadata["token_usage"]`` → 既有
  ``ContextWindowUsage``（审计/事件/成本统一用量对象）。

转换规则（contracts/messages.md §2）：tool_call_id / tool_calls / name 双向保真；
tool_calls 的 OpenAI 格式（function.arguments JSON 字符串）↔ langchain 格式
（args dict）互转；content 支持 str 与多模态 list。
"""

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from GSagent.core.models import ContextWindowUsage


def _to_langchain_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OpenAI 格式 tool_calls → langchain 格式（args 从 JSON 字符串转 dict）。"""
    out: List[Dict[str, Any]] = []
    for tc in tool_calls or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments", "")
        try:
            args_dict = json.loads(args) if isinstance(args, str) else (args or {})
        except (json.JSONDecodeError, TypeError):
            args_dict = {}
        out.append(
            {
                "name": fn.get("name", ""),
                "args": args_dict,
                "id": tc.get("id", ""),
                "type": tc.get("type", "tool_call"),
            }
        )
    return out


def _to_openai_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """langchain 格式 tool_calls → OpenAI 格式（arguments 转 JSON 字符串）。"""
    out: List[Dict[str, Any]] = []
    for tc in tool_calls or []:
        out.append(
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False),
                },
            }
        )
    return out


def dict_to_messages(dicts: List[Dict[str, Any]]) -> List[BaseMessage]:
    """OpenAI 格式消息 dict 列表 → langchain BaseMessage 列表。"""
    out: List[BaseMessage] = []
    for m in dicts or []:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "assistant":
            out.append(
                AIMessage(
                    content=content,
                    tool_calls=_to_langchain_tool_calls(m.get("tool_calls") or []),
                )
            )
        elif role == "tool":
            out.append(
                ToolMessage(
                    content=content,
                    tool_call_id=m.get("tool_call_id", ""),
                    name=m.get("name", ""),
                )
            )
        else:  # user 及未知 → HumanMessage
            out.append(HumanMessage(content=content))
    return out


def messages_to_dict(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """langchain BaseMessage 列表 → OpenAI 格式消息 dict 列表。"""
    out: List[Dict[str, Any]] = []
    for m in messages or []:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content})
        elif isinstance(m, AIMessage):
            item: Dict[str, Any] = {"role": "assistant", "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = _to_openai_tool_calls(m.tool_calls)
            out.append(item)
        elif isinstance(m, ToolMessage):
            out.append(
                {
                    "role": "tool",
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                    "name": m.name or "",
                }
            )
        else:
            out.append({"role": "user", "content": m.content})
    return out


def extract_usage(aimessage: AIMessage) -> ContextWindowUsage:
    """从 AIMessage.response_metadata["token_usage"] 提取用量（缺省全 0）。"""
    meta = getattr(aimessage, "response_metadata", None) or {}
    usage = meta.get("token_usage") or {}
    if not isinstance(usage, dict):
        return ContextWindowUsage()
    return ContextWindowUsage(
        total_tokens=int(usage.get("total_tokens", 0) or 0),
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        cache_read=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_write=int(usage.get("cache_creation_input_tokens", 0) or 0),
        reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0),
    )


__all__ = [
    "dict_to_messages",
    "extract_usage",
    "messages_to_dict",
]
