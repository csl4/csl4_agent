"""为智能体提供提示词构建工具，支持多层组装。

系统提示词由相互独立的组件组装而成，每个组件均可通过
PromptComponent 枚举 + behavior_controls 字典单独开关。这样不同的
部署场景（CLI 与 server）就可以包含不同的提示词片段。
"""

# ======================= 中文导览 =======================
# 本文件是【系统提示词组装器】：
#   build_system_prompt → 依次拼装 8 个独立组件(见 prompt_components.py)，成完整 system prompt。
#                         每个组件可按 behavior_controls 独立开关、按 custom_components 覆盖。
#   build_user_prompt   → 把用户问句(+可选图片)拼成 user 消息的 content parts(支持多模态)。
#   build_tools_description → 把 toolsets 渲染成人类可读的工具清单，塞进 TOOLSET_INSTRUCTIONS。
# 设计理念：提示词拆成「表驱动」的组件，适配 CLI / server 不同部署只需开关组件而非改 prompt 文本。
# =========================================================

from typing import Any, Dict, List, Optional

from agent.core.prompt_components import (
    DEFAULT_PROMPT_COMPONENTS,
    PROMPT_COMPONENT_ORDER,
    PromptComponent,
)


def build_tools_description(toolsets: List[Any]) -> str:
    """构建可用工具的人类可读描述。

    参数:
        toolsets: Toolset 对象列表。

    返回:
        描述所有可用工具的格式化字符串。
    """
    lines: List[str] = []

    for toolset in toolsets:
        lines.append(f"## {toolset.name}")
        lines.append(toolset.description)
        lines.append("")
        for tool in toolset.tools:
            params_desc = _format_parameters(tool.parameters)
            lines.append(f"- **{tool.name}**: {tool.description}")
            if params_desc:
                lines.append(f"  Parameters: {params_desc}")
        lines.append("")

    return "\n".join(lines)


def _format_parameters(parameters: Dict[str, Any]) -> str:
    """将工具参数格式化为可读字符串。"""
    parts = []
    for name, param in parameters.items():
        required = "(required)" if getattr(param, "required", False) else "(optional)"
        desc = getattr(param, "description", "")
        parts.append(f"{name} {required}: {desc}")
    return ", ".join(parts)


def _simple_builder(component: PromptComponent):
    """为只需要「自定义或默认」的组件构建 builder 函数。"""
    return lambda custom=None: custom or DEFAULT_PROMPT_COMPONENTS[component]


def _build_skills(skills: Optional[List[str]] = None, custom: Optional[str] = None) -> str:
    """构建 skills 组件。"""
    if custom:
        return custom
    if skills:
        lines = ["## Skills"]
        for skill in skills:
            lines.append(f"- {skill}")
        return "\n".join(lines)
    return ""


def _build_toolset_instructions(
    toolsets: List[Any], custom: Optional[str] = None
) -> str:
    """构建 toolset 使用说明组件。"""
    if custom:
        return custom
    if not toolsets:
        return ""
    tools_desc = build_tools_description(toolsets)
    return f"## Available Tools\n{tools_desc}"


def _build_general_instructions(
    global_instructions: Optional[str] = None, custom: Optional[str] = None
) -> str:
    """构建通用指令组件。"""
    if custom:
        return custom
    if global_instructions:
        return f"## Instructions\n{global_instructions}"
    return DEFAULT_PROMPT_COMPONENTS[PromptComponent.GENERAL_INSTRUCTIONS]


def _build_system_prompt_additions(additions: Optional[str] = None) -> str:
    """构建系统提示词追加内容组件。"""
    return additions or ""


# Map PromptComponent to its builder function.
# Components that only need `custom or DEFAULT_PROMPT_COMPONENTS[component]`
# use the shared _simple_builder factory instead of one function each.
_COMPONENT_BUILDERS = {
    PromptComponent.INTRO: _simple_builder(PromptComponent.INTRO),
    PromptComponent.SKILLS: _build_skills,
    PromptComponent.TODOWRITE_INSTRUCTIONS: _simple_builder(
        PromptComponent.TODOWRITE_INSTRUCTIONS
    ),
    PromptComponent.TOOLSET_INSTRUCTIONS: _build_toolset_instructions,
    PromptComponent.GENERAL_INSTRUCTIONS: _build_general_instructions,
    PromptComponent.PERMISSION_ERRORS: _simple_builder(
        PromptComponent.PERMISSION_ERRORS
    ),
    PromptComponent.STYLE_GUIDE: _simple_builder(PromptComponent.STYLE_GUIDE),
    PromptComponent.SYSTEM_PROMPT_ADDITIONS: _build_system_prompt_additions,
}


# 表驱动核心：组件枚举 → builder 函数映射，build_system_prompt 按序遍历调用。
def build_system_prompt(
    toolsets: Optional[List[Any]] = None,
    global_instructions: Optional[str] = None,
    skills: Optional[List[str]] = None,
    behavior_controls: Optional[Dict[PromptComponent, bool]] = None,
    custom_components: Optional[Dict[PromptComponent, str]] = None,
    system_prompt_additions: Optional[str] = None,
) -> str:
    """多层系统提示词组装。

    组装顺序：
        INTRO → SKILLS → TODOWRITE_INSTRUCTIONS → GENERAL_INSTRUCTIONS
        → TOOLSET_INSTRUCTIONS → PERMISSION_ERRORS → STYLE_GUIDE
        → SYSTEM_PROMPT_ADDITIONS

    每个组件都可以通过 behavior_controls 独立开关。
    自定义内容可以通过 custom_components 注入。

    参数:
        toolsets: 可用 Toolset 对象列表。
        global_instructions: 可选的全局指令/护栏（guardrails）。
        skills: 可选的技能描述列表。
        behavior_controls: 将 PromptComponent 映射到 bool 的字典（True = 包含）。
            默认所有组件均启用。
        custom_components: 将 PromptComponent 映射到自定义内容字符串的字典。
            覆盖该组件的默认内容。
        system_prompt_additions: 可选的用户自定义额外指令，追加在
            系统提示词末尾。

    返回:
        完整的系统提示词字符串。
    """
    if toolsets is None:
        toolsets = []

    if behavior_controls is None:
        # Default: all components enabled
        behavior_controls = {comp: True for comp in PromptComponent}

    if custom_components is None:
        custom_components = {}

    parts: List[str] = []

    for component in PROMPT_COMPONENT_ORDER:
        # Check if this component is enabled
        if not behavior_controls.get(component, True):
            continue

        builder = _COMPONENT_BUILDERS.get(component)
        if builder is None:
            continue

        custom = custom_components.get(component)

        # Different builders have different signatures
        if component == PromptComponent.SKILLS:
            content = builder(skills=skills, custom=custom)
        elif component == PromptComponent.TOOLSET_INSTRUCTIONS:
            content = builder(toolsets=toolsets, custom=custom)
        elif component == PromptComponent.GENERAL_INSTRUCTIONS:
            content = builder(global_instructions=global_instructions, custom=custom)
        elif component == PromptComponent.SYSTEM_PROMPT_ADDITIONS:
            content = builder(additions=system_prompt_additions or custom)
        else:
            content = builder(custom=custom)

        if content:
            parts.append(content)

    return "\n\n".join(parts)


def build_user_prompt(
    user_input: str,
    images: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """构建用户消息，可选择附带图片。

    参数:
        user_input: 用户的文本输入。
        images: 可选的图片字典列表，包含 'url' 或 'base64' 键。

    返回:
        用户消息的 content parts 列表。
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_input}]

    if images:
        for img in images:
            if "url" in img:
                content.append({"type": "image_url", "image_url": {"url": img["url"]}})
            elif "base64" in img:
                data_uri = f"data:image/{img.get('format', 'png')};base64,{img['base64']}"
                content.append({"type": "image_url", "image_url": {"url": data_uri}})

    return content


