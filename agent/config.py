"""智能体的配置加载与工厂方法。"""


# ======================= 中文导览 =======================
# 本文件是【装配根 / 装配工】：唯一的「组合根」，从这里把整台机器组装好。
#   Config.create_llm()              → 造 LLM provider
#   Config.create_tool_executor()    → 载内置 Python 工具集 + YAML 工具集，打包成 ToolExecutor
#   Config.create_tool_calling_llm() → 把 LLM + ToolExecutor + compactor + limiter 组装成主循环
# 设计理念：
#   ① 依赖是「构造函数注入」，不内部 new —— main.py 从这里拿拼好的对象。
#   ② 工具集多元化：内置 Python 模块 + YAML 模板文件都能注册进同一 ToolExecutor。
#   ③ 配置三层覆盖：默认值 → YAML 文件 → 环境变量（_deep_merge）。
# =========================================================


import copy
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
        "model": "deepseek/deepseek-v4-flash",
        "api_key": "",
        "base_url": "https://api.deepseek.com",
    },
    "agent": {
        "max_steps": 20,
        "tool_results_dir": "/tmp/agent_tool_results",
        "global_instructions": "",
        "enable_compaction": True,
        "compaction_threshold_ratio": 0.75,
        "compaction_keep_last_n": 6,
    },
    # Per-toolset config sections; see each toolset's config class for fields.
    "bash": {},
    "toolsets": [],
}


class Config:
    """智能体配置，从 ~/.agent/config.yaml 加载，并支持环境变量覆盖。"""

    # ---- 装配工（composition root）----

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_FILE # 用户目录下加载
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        """从 YAML 文件加载配置，回退到默认值。"""
        # Deep copy: dict() would only copy the top level, leaving the nested
        # llm/agent/bash dicts as shared references to DEFAULT_CONFIG. The
        # merge/env/override steps below mutate in place, so a shallow copy
        # would leak one Config instance's values into the next one.
        config = copy.deepcopy(DEFAULT_CONFIG)
        print("默认配置",config)


        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                    print("用户的配置",user_config)
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
        config["agent"]["max_steps"] = self._env_int(
            "AGENT_MAX_STEPS", config["agent"]["max_steps"]
        )
        config["agent"]["tool_results_dir"] = os.getenv(
            "AGENT_TOOL_RESULTS_DIR", config["agent"]["tool_results_dir"]
        )

        return config

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        """读取整数类型的环境变量；遇到非法值时回退到默认值并给出警告。"""
        raw = os.getenv(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                f"Environment variable {name}={raw!r} is not an integer; "
                f"using default {default}."
            )
            return default

    @staticmethod
    # 三层覆盖核心：默认值 ← 被 YAML 用户配置覆盖 ← 被环境变量覆盖。
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """将 override 递归合并到 base 中。"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value

    def create_llm(self) -> LLM:
        """根据配置创建 LLM provider。"""
        llm_config = self.data["llm"]
        return LiteLLMProvider(
            model=llm_config["model"],
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
        )

    def create_tool_executor(
        self,
        toolsets: Optional[List[Toolset]] = None, # 显式传入
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
    ) -> ToolExecutor:
        """根据配置创建 ToolExecutor，可选附加额外的 toolsets。

        按以下来源加载 toolsets：
        1. 显式传入的 toolsets 列表
        2. agent/plugins/toolsets/*.yaml 中的 YAML 文件
        3. plugins/toolsets/__init__.py 的 BUILTIN_PYTHON_TOOLSETS 注册表：
           每个工具集类（工厂）自己创建实例，
           config.yaml 对应段（如 `bash:`）作为 install config 传入

        参数:
            toolsets: 可选的预先创建的 Toolset 实例列表。
            toolset_tag_filter: 可选的标签过滤器。只加载至少匹配一个标签的
                toolsets。None 表示不过滤。
                CLI 模式： [ToolsetTag.CORE, ToolsetTag.CLI]



                 Server 模式： [ToolsetTag.CORE, ToolsetTag.CLUSTER]

        返回:
            配置好的 ToolExecutor 实例。
        """
        all_toolsets: List[Toolset] = list(toolsets or []) # 如果为空

        for name, factory in BUILTIN_PYTHON_TOOLSETS.items(): # 项目工具集合

            try:
                toolset = factory(self.data.get(name) or None) #
                if toolset:
                    all_toolsets.append(toolset)
            except Exception as e:
                logger.warning(f"Failed to load builtin toolset '{name}': {e}")

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
    ) -> ToolCallingLLM: #
        """根据配置创建 ToolCallingLLM 实例。

        参数:
            tool_executor: 可选的预先配置好的 ToolExecutor。
            llm: 可选的预先配置好的 LLM provider。
            toolset_tag_filter: 可选的用于 ToolExecutor 的标签过滤器。

        返回:
            配置好的 ToolCallingLLM 实例。
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


