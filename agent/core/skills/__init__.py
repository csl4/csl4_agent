"""技能注入 + 环境信息采集（US3 FR-006/007，本地库）。"""

from agent.core.skills.env_info import EnvironmentInfo, collect_env_info, format_env_info
from agent.core.skills.library import (
    DEFAULT_SKILLS_DIR,
    Skill,
    SkillLibrary,
    format_skills_block,
)

__all__ = [
    "DEFAULT_SKILLS_DIR",
    "EnvironmentInfo",
    "Skill",
    "SkillLibrary",
    "collect_env_info",
    "format_env_info",
    "format_skills_block",
]
