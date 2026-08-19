"""Configuration loading and factory methods for the agent."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agent.core.llm import LLM, LiteLLMProvider
from agent.core.tool_calling_llm import ToolCallingLLM
from agent.core.tool_executor import ToolExecutor
from agent.core.tools import Toolset, ToolsetTag
from agent.plugins.toolsets import BUILTIN_PYTHON_TOOLSETS
from agent.plugins.toolsets.yaml_loader import load_yaml_toolsets

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".agent"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "model": "gpt-4o",
        "api_key": "",
        "base_url": "",
    },
    "agent": {
        "max_steps": 20,
        "tool_results_dir": "/tmp/agent_tool_results",
        "global_instructions": "",
        "enable_compaction": True,
        "compaction_threshold_ratio": 0.75,
        "compaction_keep_last_n": 6,
    },
    "toolsets": [],
}


class Config:
    """Agent configuration, loaded from ~/.agent/config.yaml with env var overrides."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_FILE
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        """Load config from YAML file, falling back to defaults."""
        config = dict(DEFAULT_CONFIG)

        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                self._deep_merge(config, user_config)
                logger.info(f"Loaded config from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")

        # Environment variable overrides
        config["llm"]["model"] = os.getenv("AGENT_MODEL", config["llm"]["model"])
        config["llm"]["api_key"] = os.getenv(
            "AGENT_API_KEY", os.getenv("OPENAI_API_KEY", config["llm"]["api_key"])
        )
        config["llm"]["base_url"] = os.getenv("AGENT_BASE_URL", config["llm"]["base_url"])
        config["agent"]["max_steps"] = int(
            os.getenv("AGENT_MAX_STEPS", config["agent"]["max_steps"])
        )
        config["agent"]["tool_results_dir"] = os.getenv(
            "AGENT_TOOL_RESULTS_DIR", config["agent"]["tool_results_dir"]
        )

        return config

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """Recursively merge override into base."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value

    def create_llm(self) -> LLM:
        """Create an LLM provider from config."""
        llm_config = self.data["llm"]
        return LiteLLMProvider(
            model=llm_config["model"],
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
        )

    def create_tool_executor(
        self,
        toolsets: Optional[List[Toolset]] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
    ) -> ToolExecutor:
        """Create a ToolExecutor from config, optionally with additional toolsets.

        Loads toolsets from:
        1. Explicitly passed toolsets list
        2. YAML files in agent/plugins/toolsets/*.yaml
        3. Python toolset modules in agent/plugins/toolsets/*/

        Args:
            toolsets: Optional list of pre-created Toolset instances.
            toolset_tag_filter: Optional tag filter. Only toolsets matching at least
                one tag will be loaded. None means no filtering.
                CLI mode: [ToolsetTag.CORE, ToolsetTag.CLI]
                Server mode: [ToolsetTag.CORE, ToolsetTag.CLUSTER]

        Returns:
            Configured ToolExecutor instance.
        """
        all_toolsets: List[Toolset] = list(toolsets or [])

        # Load builtin Python toolsets from the registry
        for factory in BUILTIN_PYTHON_TOOLSETS:
            try:
                toolset = factory()
                if toolset:
                    all_toolsets.append(toolset)
            except Exception as e:
                logger.warning(f"Failed to load builtin toolset {factory.__name__}: {e}")

        # Load YAML toolsets from the plugins/toolsets directory
        toolsets_dir = Path(__file__).parent / "plugins" / "toolsets"
        yaml_toolsets = load_yaml_toolsets(toolsets_dir)
        all_toolsets.extend(yaml_toolsets)

        return ToolExecutor(
            toolsets=all_toolsets,
            toolset_tag_filter=toolset_tag_filter,
        )

    def create_tool_calling_llm(
        self,
        tool_executor: Optional[ToolExecutor] = None,
        llm: Optional[LLM] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
    ) -> ToolCallingLLM:
        """Create a ToolCallingLLM instance from config.

        Args:
            tool_executor: Optional pre-configured ToolExecutor.
            llm: Optional pre-configured LLM provider.
            toolset_tag_filter: Optional tag filter for the ToolExecutor.

        Returns:
            Configured ToolCallingLLM instance.
        """
        agent_config = self.data["agent"]
        return ToolCallingLLM(
            tool_executor=tool_executor
            or self.create_tool_executor(toolset_tag_filter=toolset_tag_filter),
            llm=llm or self.create_llm(),
            max_steps=agent_config["max_steps"],
            tool_results_dir=agent_config["tool_results_dir"],
            enable_compaction=agent_config.get("enable_compaction", True),
            compaction_threshold_ratio=agent_config.get(
                "compaction_threshold_ratio", 0.75
            ),
            compaction_keep_last_n=agent_config.get("compaction_keep_last_n", 6),
        )