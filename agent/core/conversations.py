"""智能体的对话消息管理。"""


# ======================= 中文导览 =======================
# 本文件是【对话消息构造器】：把零散材料拼成 LLM 能吃的 messages dict 列表。
#   核心入口 build_chat_messages：
#     输入：用户问句 + 会话历史 + toolsets + 系统提示词各组件等；
#     输出：messages = [system, user, (可选历史...)] 一目了然。
# 设计要点：对话本质是「普通 dict 的列表」，不是对象 —— 它是整条数据流的主跑道，
#           所有工具结果最终都以 role:"tool" 消息压回这里，再喂给 LLM。
# =========================================================

import logging
from typing import Any, Dict, List, Optional

from agent.core.prompt import build_system_prompt, build_user_prompt
from agent.core.prompt_components import PromptComponent

logger = logging.getLogger(__name__)


def add_or_update_system_prompt(
    messages: List[Dict[str, Any]],
    system_prompt: str,
) -> List[Dict[str, Any]]:
    """在已有对话历史中更新系统提示词。

    如果第一条消息是系统消息，则替换其内容；
    否则，在开头插入一条新的系统消息。

    参数:
        messages: 已有的对话消息。
        system_prompt: 新的系统提示词内容。

    返回:
        更新了系统提示词后的消息列表。
    """
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = system_prompt
    else:
        messages.insert(0, {"role": "system", "content": system_prompt})

    return messages


# 核心入口：构造首轮 messages。内部先 build_system_prompt，再 build_user_prompt(可能含图)，
    # 再接上既有 conversation_history。
def build_chat_messages(
    ask: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    toolsets: Optional[List[Any]] = None,
    global_instructions: Optional[str] = None,
    skills: Optional[List[str]] = None,
    images: Optional[List[Dict[str, Any]]] = None,
    behavior_controls: Optional[Dict[PromptComponent, bool]] = None,
    custom_components: Optional[Dict[PromptComponent, str]] = None,
    system_prompt_additions: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """构建一次智能体调用的初始聊天消息。

    参数:
        ask: 用户的问题/请求。
        conversation_history: 可选的先前对话消息。
        toolsets: 可用 Toolset 对象列表。
        global_instructions: 可选的全局护栏（guardrails）。
        skills: 可选的技能描述。
        images: 可选的图片附件。
        behavior_controls: 将 PromptComponent 映射到 bool 的字典（True = 包含）。
        custom_components: 将 PromptComponent 映射到自定义内容的字典。
        system_prompt_additions: 用户自定义的额外指令。

    返回:
        可供 LLM 直接使用的聊天消息列表。
    """
    system_prompt = build_system_prompt(
        toolsets=toolsets or [],
        global_instructions=global_instructions,
        skills=skills,
        behavior_controls=behavior_controls,
        custom_components=custom_components,
        system_prompt_additions=system_prompt_additions,
    )

    user_content = build_user_prompt(ask, images)

    if conversation_history:
        messages = add_or_update_system_prompt(conversation_history, system_prompt)
        messages.append({"role": "user", "content": user_content})
        return messages

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]