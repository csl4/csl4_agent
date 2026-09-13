"""本地技能库：加载 / 匹配 / 管理可复用技能（FR-006，data-model.md Skill）。

技能 = 本地 YAML 定义（name/description/instructions/tool_bindings/keywords），
按任务文本做 token 重叠匹配，命中则把指令注入 Agent 提示词（FR-006）。
v1 采用轻量文件存储、无外部服务（research.md §6）。

`format_skills_block()` 把命中的技能格式化为提示词上下文块。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

from GSagent.common import DEFAULT_SKILLS_DIR

logger = logging.getLogger(__name__)

# 词元化：英文单词/数字保持整词；中文无词边界，按单字参与匹配，
# 使中文任务与描述能跨表述重叠（如"发布新版本"↔"发布"）。
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]+")


def _tokenize(text: str) -> set:
    text = (text or "").lower()
    tokens = set(_TOKEN_RE.findall(text))
    for run in _CJK_RE.findall(text):
        tokens.update(run)
    return tokens


@dataclass
class Skill:
    """一条可复用技能定义。"""

    name: str
    description: str
    instructions: str
    tool_bindings: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)


class SkillLibrary:
    """本地技能库：从目录加载 YAML 技能，按任务文本匹配（FR-006）。"""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory else DEFAULT_SKILLS_DIR
        self._skills: List[Skill] = []
        self._loaded = False

    # ---- 加载 ----
    def _load_from_disk(self) -> List[Skill]:
        if not self.directory.exists():
            return []
        skills: List[Skill] = []
        for path in sorted(self.directory.glob("*.y*ml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict) or not data.get("name"):
                    logger.warning("技能 %s 缺少 name 字段，跳过", path)
                    continue
                skills.append(
                    Skill(
                        name=str(data["name"]),
                        description=str(data.get("description", "")),
                        instructions=str(data.get("instructions", "")),
                        tool_bindings=[str(t) for t in (data.get("tool_bindings") or [])],
                        keywords=[str(k) for k in (data.get("keywords") or [])],
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 单个坏文件不影响其余技能
                logger.warning("技能加载失败 %s: %s", path, exc)
        return skills

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._skills = self._load_from_disk()
            self._loaded = True

    def _reload(self) -> None:
        self._skills = self._load_from_disk()
        self._loaded = True

    # ---- 查询 ----
    def list(self) -> List[Skill]:
        """全部技能。"""
        self._ensure_loaded()
        return list(self._skills)

    def get(self, name: str) -> Optional[Skill]:
        """按名称取技能。"""
        self._ensure_loaded()
        for skill in self._skills:
            if skill.name == name:
                return skill
        return None

    def match(self, task_text: str, limit: int = 3) -> List[Skill]:
        """按任务文本匹配技能（token 重叠度），返回按分数降序的前 limit 个。

        匹配依据：技能名 + 描述 + keywords 与任务文本的词元交集。
        """
        self._ensure_loaded()
        query = _tokenize(task_text)
        if not query:
            return []
        scored: List[tuple] = []
        for skill in self._skills:
            hay = " ".join([skill.name, skill.description, *skill.keywords])
            overlap = len(query & _tokenize(hay))
            if overlap > 0:
                scored.append((overlap, skill))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [s for _, s in scored[:limit]]

    # ---- 管理 ----
    def add(self, skill: Skill) -> Path:
        """把技能写入 YAML 文件（同名覆盖），并刷新内存。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{skill.name}.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "instructions": skill.instructions,
                    "tool_bindings": list(skill.tool_bindings),
                    "keywords": list(skill.keywords),
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self._reload()
        return path

    def remove(self, name: str) -> bool:
        """删除技能（按文件 `{name}.yaml`），返回是否删除成功。"""
        self._ensure_loaded()
        path = self.directory / f"{name}.yaml"
        if path.exists():
            path.unlink()
            self._reload()
            return True
        return False


def format_skills_block(skills: List[Skill]) -> str:
    """把命中的技能格式化为提示词上下文块（FR-006 注入用）。"""
    if not skills:
        return ""
    lines = ["可用技能（按任务自动匹配）:"]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
        if skill.instructions:
            lines.append(f"  指令: {skill.instructions}")
        if skill.tool_bindings:
            lines.append(f"  绑定工具: {', '.join(skill.tool_bindings)}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SKILLS_DIR",
    "Skill",
    "SkillLibrary",
    "format_skills_block",
]
