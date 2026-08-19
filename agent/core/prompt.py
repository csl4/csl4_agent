"""Prompt building utilities with multi-layer assembly for the agent.

The system prompt is assembled from independent components, each toggleable
via PromptComponent enum + behavior_controls dict. This allows different
deployment scenarios (CLI vs server) to include different prompt sections.
"""

from typing import Any, Dict, List, Optional

from agent.core.prompt_components import (
    DEFAULT_PROMPT_COMPONENTS,
    PROMPT_COMPONENT_ORDER,
    PromptComponent,
)


def build_tools_description(toolsets: List[Any]) -> str:
    """Build a human-readable description of available tools.

    Args:
        toolsets: List of Toolset objects.

    Returns:
        A formatted string describing all available tools.
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
    """Format tool parameters as a readable string."""
    parts = []
    for name, param in parameters.items():
        required = "(required)" if getattr(param, "required", False) else "(optional)"
        desc = getattr(param, "description", "")
        parts.append(f"{name} {required}: {desc}")
    return ", ".join(parts)


def _build_intro(custom: Optional[str] = None) -> str:
    """Build the intro/persona component."""
    return custom or DEFAULT_PROMPT_COMPONENTS[PromptComponent.INTRO]


def _build_skills(skills: Optional[List[str]] = None, custom: Optional[str] = None) -> str:
    """Build the skills component."""
    if custom:
        return custom
    if skills:
        lines = ["## Skills"]
        for skill in skills:
            lines.append(f"- {skill}")
        return "\n".join(lines)
    return ""


def _build_todowrite(custom: Optional[str] = None) -> str:
    """Build the todowrite instructions component."""
    return custom or DEFAULT_PROMPT_COMPONENTS[PromptComponent.TODOWRITE_INSTRUCTIONS]


def _build_toolset_instructions(
    toolsets: List[Any], custom: Optional[str] = None
) -> str:
    """Build the toolset instructions component."""
    if custom:
        return custom
    if not toolsets:
        return ""
    tools_desc = build_tools_description(toolsets)
    return f"## Available Tools\n{tools_desc}"


def _build_general_instructions(
    global_instructions: Optional[str] = None, custom: Optional[str] = None
) -> str:
    """Build the general instructions component."""
    if custom:
        return custom
    if global_instructions:
        return f"## Instructions\n{global_instructions}"
    return DEFAULT_PROMPT_COMPONENTS[PromptComponent.GENERAL_INSTRUCTIONS]


def _build_permission_errors(custom: Optional[str] = None) -> str:
    """Build the permission errors handling guide."""
    return custom or DEFAULT_PROMPT_COMPONENTS[PromptComponent.PERMISSION_ERRORS]


def _build_style_guide(custom: Optional[str] = None) -> str:
    """Build the style guide component."""
    return custom or DEFAULT_PROMPT_COMPONENTS[PromptComponent.STYLE_GUIDE]


def _build_system_prompt_additions(additions: Optional[str] = None) -> str:
    """Build the system prompt additions component."""
    return additions or ""


# Map PromptComponent to its builder function
_COMPONENT_BUILDERS = {
    PromptComponent.INTRO: _build_intro,
    PromptComponent.SKILLS: _build_skills,
    PromptComponent.TODOWRITE_INSTRUCTIONS: _build_todowrite,
    PromptComponent.TOOLSET_INSTRUCTIONS: _build_toolset_instructions,
    PromptComponent.GENERAL_INSTRUCTIONS: _build_general_instructions,
    PromptComponent.PERMISSION_ERRORS: _build_permission_errors,
    PromptComponent.STYLE_GUIDE: _build_style_guide,
    PromptComponent.SYSTEM_PROMPT_ADDITIONS: _build_system_prompt_additions,
}


def build_system_prompt(
    toolsets: Optional[List[Any]] = None,
    global_instructions: Optional[str] = None,
    skills: Optional[List[str]] = None,
    behavior_controls: Optional[Dict[PromptComponent, bool]] = None,
    custom_components: Optional[Dict[PromptComponent, str]] = None,
    system_prompt_additions: Optional[str] = None,
) -> str:
    """Multi-layer system prompt assembly.

    Assembly order:
        INTRO → SKILLS → TODOWRITE_INSTRUCTIONS → GENERAL_INSTRUCTIONS
        → TOOLSET_INSTRUCTIONS → PERMISSION_ERRORS → STYLE_GUIDE
        → SYSTEM_PROMPT_ADDITIONS

    Each component can be independently toggled via behavior_controls.
    Custom content can be injected via custom_components.

    Args:
        toolsets: List of available Toolset objects.
        global_instructions: Optional global instructions/guardrails.
        skills: Optional list of skill descriptions.
        behavior_controls: Dict mapping PromptComponent to bool (True = include).
            Defaults to all components enabled.
        custom_components: Dict mapping PromptComponent to custom content string.
            Overrides the default content for that component.
        system_prompt_additions: Optional user-defined extra instructions appended
            at the end of the system prompt.

    Returns:
        The complete system prompt string.
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
        if component == PromptComponent.INTRO:
            content = builder(custom=custom)
        elif component == PromptComponent.SKILLS:
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
    """Build a user message, optionally with images.

    Args:
        user_input: The user's text input.
        images: Optional list of image dicts with 'url' or 'base64' keys.

    Returns:
        A list of content parts for the user message.
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