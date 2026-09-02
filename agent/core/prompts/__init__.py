"""提示词装配（原 core/prompt.py + prompt_components.py + conversations.py）。

原 `from agent.core.prompt* / agent.core.conversations import ...` 的站点改为
`from agent.core.prompts... import ...`。
"""

from agent.core.prompts.components import (
    DEFAULT_PROMPT_COMPONENTS,
    PROMPT_COMPONENT_ORDER,
    PromptComponent,
)
from agent.core.prompts.messages import (
    add_or_update_system_prompt,
    build_chat_messages,
)
from agent.core.prompts.system import build_system_prompt, build_tools_description
from agent.core.prompts.user import build_user_prompt

__all__ = [
    "DEFAULT_PROMPT_COMPONENTS",
    "PROMPT_COMPONENT_ORDER",
    "PromptComponent",
    "add_or_update_system_prompt",
    "build_chat_messages",
    "build_system_prompt",
    "build_tools_description",
    "build_user_prompt",
]
