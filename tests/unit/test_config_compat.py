"""配置向后兼容单测（宪法 III）：旧配置/新配置加载行为。"""

from pathlib import Path

import pytest

from agent.config import Config, DEFAULT_CONFIG


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


class TestOldConfigCompat:
    def test_old_config_without_multi_agent_loads(self, tmp_path: Path) -> None:
        """旧版 config.yaml（无 multi_agent 段）加载零告警、默认值生效。"""
        path = _write_config(
            tmp_path,
            "llm:\n  model: test-model\nagent:\n  max_steps: 5\n",
        )
        config = Config(config_path=path)
        settings = config.multi_agent_settings()
        assert settings["enabled"] is False
        assert settings["max_subagents"] == DEFAULT_CONFIG["multi_agent"]["max_subagents"]

    def test_empty_config_loads_defaults(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "")
        config = Config(config_path=path)
        assert config.data["llm"]["model"] == DEFAULT_CONFIG["llm"]["model"]
        assert config.multi_agent_settings()["enabled"] is False

    def test_no_config_file_loads_defaults(self, tmp_path: Path) -> None:
        config = Config(config_path=tmp_path / "missing.yaml")
        assert config.data["agent"]["max_steps"] == DEFAULT_CONFIG["agent"]["max_steps"]

    def test_existing_keys_unchanged(self, tmp_path: Path) -> None:
        """已有配置项不受 multi_agent 新增影响。"""
        path = _write_config(
            tmp_path,
            "agent:\n  max_steps: 9\n  enable_compaction: false\n",
        )
        config = Config(config_path=path)
        assert config.data["agent"]["max_steps"] == 9
        assert config.data["agent"]["enable_compaction"] is False


class TestMultiAgentConfig:
    def test_enabled_true(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path,
            "multi_agent:\n  enabled: true\n  max_subagents: 6\n",
        )
        config = Config(config_path=path)
        settings = config.multi_agent_settings()
        assert settings["enabled"] is True
        assert settings["max_subagents"] == 6

    def test_partial_section_merges_defaults(self, tmp_path: Path) -> None:
        """只写部分字段 → 其余回退默认。"""
        path = _write_config(tmp_path, "multi_agent:\n  enabled: true\n")
        config = Config(config_path=path)
        settings = config.multi_agent_settings()
        assert settings["sandbox"]["type"] == "lightweight"
        assert settings["a2a"]["transport"] == "in-process"

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_MULTI_AGENT", "1")
        monkeypatch.setenv("AGENT_MAX_SUBAGENTS", "8")
        config = Config(config_path=tmp_path / "missing.yaml")
        settings = config.multi_agent_settings()
        assert settings["enabled"] is True
        assert settings["max_subagents"] == 8

    def test_env_bool_invalid_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_MULTI_AGENT", "not-a-bool")
        config = Config(config_path=tmp_path / "missing.yaml")
        assert config.multi_agent_settings()["enabled"] is False
