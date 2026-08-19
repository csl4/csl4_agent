"""Conversation message management for the agent."""

import logging
from typing import Any, Dict, List, Optional

from agent.core.prompt import build_system_prompt, build_user_prompt
from agent.core.prompt_components import PromptComponent

logger = logging.getLogger(__name__)


def add_or_update_system_prompt(
    messages: List[Dict[str, Any]],
    system_prompt: str,
) -> List[Dict[str, Any]]:
    """Update the system prompt in an existing conversation history.

    If the first message is a system message, replace its content.
    Otherwise, insert a new system message at the beginning.

    Args:
        messages: Existing conversation messages.
        system_prompt: The new system prompt content.

    Returns:
        Modified messages list with updated system prompt.
    """
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = system_prompt
    else:
        messages.insert(0, {"role": "system", "content": system_prompt})

    return messages


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
    """Build the initial chat messages for an agent call.

    Args:
        ask: The user's question/request.
        conversation_history: Optional previous conversation messages.
        toolsets: List of available Toolset objects.
        global_instructions: Optional global guardrails.
        skills: Optional skill descriptions.
        images: Optional image attachments.
        behavior_controls: Dict mapping PromptComponent to bool (True = include).
        custom_components: Dict mapping PromptComponent to custom content.
        system_prompt_additions: User-defined extra instructions.

    Returns:
        List of chat messages ready for LLM consumption.
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