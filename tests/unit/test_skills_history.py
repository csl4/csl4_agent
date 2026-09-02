"""技能库匹配、环境信息采集、历史存储/查询单测（T027，FR-006/007/008）。"""

import os
from pathlib import Path

import pytest

from agent.core.history.store import HistoryRecord, HistoryStore
from agent.core.skills.env_info import collect_env_info, format_env_info
from agent.core.skills.library import Skill, SkillLibrary, format_skills_block


def _write_skill(directory: Path, name: str, description: str, keywords=None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = (
        f"name: {name}\n"
        f"description: {description}\n"
        "instructions: 按技能步骤执行。\n"
        "tool_bindings: [bash]\n"
    )
    if keywords:
        text += "keywords: [" + ", ".join(f'"{k}"' for k in keywords) + "]\n"
    (directory / f"{name}.yaml").write_text(text, encoding="utf-8")


class TestSkillLibrary:
    def test_load_skills_from_directory(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "deploy", "发布应用新版本", ["deploy", "发布"])
        _write_skill(tmp_path, "backup", "备份数据库", ["backup", "备份"])
        lib = SkillLibrary(directory=tmp_path)
        assert len(lib.list()) == 2

    def test_match_skill_by_keywords(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "deploy", "发布应用新版本", ["deploy", "发布"])
        _write_skill(tmp_path, "backup", "备份数据库", ["backup", "备份"])
        lib = SkillLibrary(directory=tmp_path)
        matched = lib.match("请帮我发布新版本到生产环境")
        assert matched and matched[0].name == "deploy"

    def test_match_unrelated_returns_empty(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "deploy", "发布应用新版本", ["deploy", "发布"])
        lib = SkillLibrary(directory=tmp_path)
        assert lib.match("帮我查一下天气") == []

    def test_add_writes_yaml(self, tmp_path: Path) -> None:
        lib = SkillLibrary(directory=tmp_path)
        skill = Skill(
            name="init-project",
            description="初始化项目结构",
            instructions="创建 README 与目录。",
            tool_bindings=["bash"],
            keywords=["init", "初始化"],
        )
        path = lib.add(skill)
        assert path.exists()
        assert lib.get("init-project") is not None

    def test_remove_deletes(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "deploy", "发布应用", ["deploy"])
        lib = SkillLibrary(directory=tmp_path)
        assert lib.remove("deploy") is True
        assert lib.get("deploy") is None
        assert lib.remove("missing") is False

    def test_missing_directory_loads_empty(self, tmp_path: Path) -> None:
        lib = SkillLibrary(directory=tmp_path / "nope")
        assert lib.list() == []

    def test_format_skills_block(self) -> None:
        skills = [
            Skill(name="deploy", description="发布应用", instructions="跑部署脚本。", tool_bindings=["bash"])
        ]
        block = format_skills_block(skills)
        assert "deploy" in block
        assert "跑部署脚本" in block


class TestEnvironmentInfo:
    def test_collect_snapshot(self) -> None:
        info = collect_env_info()
        assert info.cwd == os.getcwd()
        assert info.shell in ("bash", "zsh", "powershell")
        assert info.platform

    def test_collect_with_tool_names(self) -> None:
        info = collect_env_info(tool_names=["bash", "sandbox"])
        assert info.available_tools == ["bash", "sandbox"]

    def test_format_contains_fields(self) -> None:
        info = collect_env_info(tool_names=["bash"])
        text = format_env_info(info)
        assert "当前环境信息" in text
        assert info.cwd in text
        assert "bash" in text


class TestHistoryStore:
    def test_append_and_query_session(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.jsonl")
        rec = HistoryRecord.new(
            "command", session_id="s-1", agent_id="sub-1",
            payload={"command": "df -h"},
        )
        store.append(rec)
        store.append(HistoryRecord.new("event", session_id="s-1", payload={"note": "x"}))
        found = store.query_session("s-1")
        assert len(found) == 2
        assert found[0].payload["command"] == "df -h"

    def test_append_command_and_query_pattern(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.jsonl")
        store.append_command(command="df -h", session_id="s-2", shell="bash")
        store.append_command(command="du -sh .", session_id="s-2", shell="bash")
        found = store.query_command("df")
        assert len(found) == 1
        assert found[0].payload["command"] == "df -h"

    def test_no_file_returns_empty(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "missing.jsonl")
        assert store.query_session("s-x") == []
        assert store.query_command("anything") == []

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "history.jsonl"
        path.write_text("not-json\n", encoding="utf-8")
        store = HistoryStore(path=path)
        assert store.query_session("s-x") == []

    def test_list_recent(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.jsonl")
        for i in range(3):
            store.append(HistoryRecord.new("event", session_id=f"s-{i}", payload={"i": i}))
        recent = store.list_recent(limit=2)
        assert len(recent) == 2
        assert recent[-1].session_id == "s-2"
