"""提示词组件定义 —— 枚举、默认值和组装顺序。"""


# ======================= 中文导览 =======================
# 本文件定义系统提示词的「零件清单」：
#   PromptComponent      → 8 个组件的枚举（每个独立可开关/覆盖）
#   PROMPT_COMPONENT_ORDER → 拼装顺序（越靠前的组件在 prompt 里越靠上）
#   DEFAULT_PROMPT_COMPONENTS → 各组件默认文本（开箱即用，可被 custom_components 覆盖）
# 与 prompt.py 配合：这里定义「有哪些零件+顺序+默认值」，prompt.py 负责按顺序组装。
# =========================================================

from enum import Enum
from typing import Dict, List


class PromptComponent(str, Enum):
    """系统提示词组件，每个均可通过 behavior_controls 独立开关。

    不同的部署场景包含不同的组件：
    - CLI 模式：通常包含 INTRO、TOOLSET_INSTRUCTIONS、GENERAL_INSTRUCTIONS
    - Server 模式：可能额外加入 TODOWRITE_INSTRUCTIONS、PERMISSION_ERRORS、STYLE_GUIDE
    """

    INTRO = "intro"
    SKILLS = "skills"
    TODOWRITE_INSTRUCTIONS = "todowrite_instructions"
    TOOLSET_INSTRUCTIONS = "toolset_instructions"
    GENERAL_INSTRUCTIONS = "general_instructions"
    PERMISSION_ERRORS = "permission_errors"
    STYLE_GUIDE = "style_guide"
    SYSTEM_PROMPT_ADDITIONS = "system_prompt_additions"


# The order in which components are assembled into the system prompt.
# Order matters — earlier components appear first in the prompt.
PROMPT_COMPONENT_ORDER: List[PromptComponent] = [
    PromptComponent.INTRO,
    PromptComponent.SKILLS,
    PromptComponent.TODOWRITE_INSTRUCTIONS,
    PromptComponent.GENERAL_INSTRUCTIONS,
    PromptComponent.TOOLSET_INSTRUCTIONS,
    PromptComponent.PERMISSION_ERRORS,
    PromptComponent.STYLE_GUIDE,
    PromptComponent.SYSTEM_PROMPT_ADDITIONS,
]

# Default content for each component.
# These provide sensible defaults so the agent works out of the box,
# but can be overridden via custom_components or config.
DEFAULT_PROMPT_COMPONENTS: Dict[PromptComponent, str] = {
    PromptComponent.INTRO: (
        "You are an AI assistant with access to tools. "
        "Use the tools to help users accomplish their tasks. "
        "When you need information, call the appropriate tool. "
        "When you have enough information to answer, provide a clear response."
    ),
    PromptComponent.TODOWRITE_INSTRUCTIONS: (
        "## Task Management\n"
        "Use the TodoWrite tool to track your progress on multi-step tasks. "
        "Create a task list at the beginning and update it as you work. "
        "Mark tasks as completed when done."
    ),
    PromptComponent.GENERAL_INSTRUCTIONS: (
        "## Investigation Methodology\n"
        "When investigating an issue or answering a question:\n"
        "1. Understand the user's question thoroughly — ask clarifying questions if needed.\n"
        "2. Identify which tools are relevant to the task.\n"
        "3. Call tools with precise parameters — be specific, not vague.\n"
        "4. Analyze results before responding — don't just echo raw data.\n"
        "5. If a tool returns an error, try to correct the parameters and retry once.\n"
        "6. If retrying doesn't help, explain the error to the user and ask for guidance.\n"
        "7. Provide a clear, concise answer with supporting evidence.\n"
        "8. Think step by step — break complex problems into smaller sub-tasks."
    ),
    PromptComponent.PERMISSION_ERRORS: (
        "## Handling Permission Errors\n"
        "If a tool returns a permission error or authorization failure:\n"
        "1. Do NOT retry the same call with the same parameters.\n"
        "2. Explain to the user what permission is missing.\n"
        "3. Suggest alternative approaches that don't require those permissions.\n"
        "4. Wait for the user to grant access before retrying."
    ),
    PromptComponent.STYLE_GUIDE: (
        "## Response Style\n"
        "- Be concise but thorough — don't leave out important details.\n"
        "- Use markdown formatting for readability (headers, lists, code blocks).\n"
        "- When showing code or commands, use code blocks with language tags.\n"
        "- When showing data, use tables or structured formatting.\n"
        "- Acknowledge uncertainty — don't pretend to know something you don't.\n"
        "- If you made a mistake, admit it and correct yourself."
    ),
}