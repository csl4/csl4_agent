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


class TestSandboxConfig:
    def test_multi_agent_sandbox_overrides_top_level(self, tmp_path: Path) -> None:
        """multi_agent.sandbox 覆盖顶层 sandbox:（T025，contracts/config.md）。"""
        path = _write_config(
            tmp_path,
            "multi_agent:\n  sandbox:\n    timeout_seconds: 60\n",
        )
        config = Config(config_path=path)
        settings = config.sandbox_settings()
        assert settings["timeout_seconds"] == 60
        assert settings["type"] == "lightweight"  # 未覆盖项回退默认
        # 顶层 `sandbox:` 也被合并（工具集工厂读取顶层段）
        assert config.data["sandbox"]["timeout_seconds"] == 60

    def test_top_level_sandbox_defaults(self, tmp_path: Path) -> None:
        """只有顶层 sandbox: 段时，默认值生效。"""
        path = _write_config(tmp_path, "sandbox:\n  type: lightweight\n")
        config = Config(config_path=path)
        assert config.data["sandbox"]["timeout_seconds"] == 30
        assert config.sandbox_settings()["type"] == "lightweight"

    def test_no_sandbox_config_defaults(self, tmp_path: Path) -> None:
        """无任何 sandbox 配置 → 默认 lightweight 沙箱。"""
        config = Config(config_path=tmp_path / "missing.yaml")
        settings = config.sandbox_settings()
        assert settings["type"] == "lightweight"
        assert settings["timeout_seconds"] == 30


class TestApplyOverrides:
    """CLI 覆盖层（最高优先级）：Config.apply_overrides 行为。"""

    def test_llm_overrides(self, tmp_path: Path) -> None:
        config = Config(config_path=tmp_path / "missing.yaml")
        config.apply_overrides(api_key="k", model="m", base_url="u")
        assert config.data["llm"]["api_key"] == "k"
        assert config.data["llm"]["model"] == "m"
        assert config.data["llm"]["base_url"] == "u"

    def test_agent_overrides(self, tmp_path: Path) -> None:
        config = Config(config_path=tmp_path / "missing.yaml")
        config.apply_overrides(max_steps=7, no_compaction=True)
        assert config.data["agent"]["max_steps"] == 7
        assert config.data["agent"]["enable_compaction"] is False

    def test_no_args_keeps_defaults(self, tmp_path: Path) -> None:
        config = Config(config_path=tmp_path / "missing.yaml")
        before = dict(config.data["llm"])
        config.apply_overrides()
        assert config.data["llm"] == before
        assert config.data["agent"]["enable_compaction"] is True

    def test_cli_overrides_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """四层优先级：CLI > 环境变量 > YAML > 默认值。"""
        monkeypatch.setenv("AGENT_MODEL", "env-model")
        config = Config(config_path=tmp_path / "missing.yaml")
        assert config.data["llm"]["model"] == "env-model"
        config.apply_overrides(model="cli-model")
        assert config.data["llm"]["model"] == "cli-model"


class TestEnvOverrides:
    """环境变量覆盖层（第三层）：声明式表 _ENV_OVERRIDES 回归。"""

    def test_model_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_MODEL", "env-model")
        config = Config(config_path=tmp_path / "missing.yaml")
        assert config.data["llm"]["model"] == "env-model"

    def test_api_key_env_prefers_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_API_KEY", "agent-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        config = Config(config_path=tmp_path / "missing.yaml")
        assert config.data["llm"]["api_key"] == "agent-key"

    def test_api_key_env_falls_back_openai(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENT_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        config = Config(config_path=tmp_path / "missing.yaml")
        assert config.data["llm"]["api_key"] == "openai-key"
