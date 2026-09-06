"""智能体的配置加载与工厂方法。"""


# ======================= 中文导览 =======================
# 本文件是【装配根 / 装配工】：唯一的「组合根」，从这里把整台机器组装好。
#   Config.create_llm()              → 造 LLM provider
#   Config.create_tool_executor()    → 载内置 Python 工具集 + YAML 工具集，打包成 ToolExecutor
#   Config.create_tool_calling_llm() → 把 LLM + ToolExecutor + compactor + limiter 组装成主循环
# 设计理念：
#   ① 依赖是「构造函数注入」，不内部 new —— main.py 从这里拿拼好的对象。
#   ② 工具集多元化：内置 Python 模块 + YAML 模板文件都能注册进同一 ToolExecutor。
#   ③ 配置四层覆盖（优先级从低到高）：默认值 → YAML 文件 → 环境变量
#      （_ENV_OVERRIDES 声明式表）→ CLI 覆盖（Config.apply_overrides）。
# =========================================================


import copy
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from agent.core.cost import CostEstimator
from agent.core.policy import AuditLog, CommandGuard, HitlPolicy, PathGuard
from agent.core.providers import LLM, LiteLLMProvider
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
        "global_instructions": "",
        "enable_compaction": True,
        "compaction_threshold_ratio": 0.75,
        "compaction_keep_last_n": 6,
        "record_usage": True,  # 企业级：是否记录用量/成本（FR-007）
    },
    # 企业级安全策略层（002-enterprise-cli-upgrade，contracts/config.md）。
    # 只增不改：新增段不影响既有 llm/agent/bash/sandbox/multi_agent。
    "policy": {
        "hitl_mode": "auto",  # auto|always|never，默认 auto（FR-003）
        "workspace_root": "",  # 工作区根；空=运行时当前目录（FR-001）
        "command_blacklist": [],  # 额外破坏性命令正则（FR-002）
        "command_allowlist": [],  # 守卫显式放行正则（FR-002 出口）
        "audit_dir": "",  # 审计 JSONL 目录；空=~/.agent/audit（FR-005）
        "snapshot_dir": "",  # 快照目录；空=~/.agent/snapshots（FR-012）
        "approval_window_sec": 60,  # 审批超时窗口，超时默认拒绝
    },
    "runtime": {
        "queue_db": "",  # 持久化任务队列 SQLite；空=~/.agent/runtime.db（FR-009）
        "serve_port": 8000,  # serve 监听端口（FR-011）
    },
    "cost": {
        # 本地定价表 {model: {prompt_per_1k, completion_per_1k}}；空=只记 token（R-06）
        "pricing": {},
    },
    "eval": {
        "datasets_dir": "eval/datasets",  # 评估数据集目录（FR-013）
        "baseline_dir": "eval/baselines",  # 基线报告目录
    },
    # Per-toolset config sections; see each toolset's config class for fields.
    "bash": {},
    "sandbox": {
        # 轻量沙箱工具集（US2 FR-003/004）；multi_agent.sandbox 可覆盖同名项。
        "type": "lightweight",  # v1: lightweight；后续 container
        "timeout_seconds": 30,
        "working_dir": "",
        "builtin_allowlist": "extended",
    },
    "toolsets": [],
    # 多Agent 编排配置（宪法 III：新增字段，不改动既有字段名，向后兼容）。
    "multi_agent": {
        "enabled": False,  # 默认关闭，保持单Agent 行为
        "max_subagents": 4,  # 单任务最大并行 SubAgent 数
        "orchestrator_model": "deepseek/deepseek-v4-flash",  # 空则复用 llm.model
        "a2a": {"transport": "in-process"},
        "sandbox": {"type": "lightweight", "timeout_seconds": 30, "working_dir": ""},
    },
}


def _env_str(raw: str, current: Any) -> Any:
    """字符串环境变量：原样返回（占位转换器，统一表项签名）。"""
    return raw


def _env_bool(raw: str, current: Any) -> Any:
    """把环境变量字符串解析为布尔；空串/非法值回退当前值。"""
    if raw is None or raw == "":
        return current
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(raw: str, current: Any) -> Any:
    """把环境变量字符串解析为整数；非法值回退当前值并给出警告。"""
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            f"Environment variable value {raw!r} is not an integer; "
            f"using current value {current!r}."
        )
        return current


# 环境变量覆盖层（第三层：默认值 ← YAML ← 环境变量 ← CLI）。
# 每项: (配置点路径, 环境变量名或元组, 转换器)。环境变量名用元组时按顺序取
# 第一个已设置的（兼容回退，如 OPENAI_API_KEY）。转换器签名: (raw, current) -> value。
_ENV_OVERRIDES: List[Tuple[str, Any, Callable[[str, Any], Any]]] = [
    ("llm.model", "AGENT_MODEL", _env_str),
    ("llm.api_key", ("AGENT_API_KEY", "OPENAI_API_KEY"), _env_str),
    ("llm.base_url", "AGENT_BASE_URL", _env_str),
    ("agent.max_steps", "AGENT_MAX_STEPS", _env_int),
    ("multi_agent.enabled", "AGENT_MULTI_AGENT", _env_bool),
    ("multi_agent.max_subagents", "AGENT_MAX_SUBAGENTS", _env_int),
    # 企业级升级新增（002-enterprise-cli-upgrade，contracts/config.md）
    ("policy.hitl_mode", "AGENT_HITL_MODE", _env_str),
    ("policy.workspace_root", "AGENT_WORKSPACE_ROOT", _env_str),
    ("agent.record_usage", "AGENT_RECORD_USAGE", _env_bool),
    ("runtime.serve_port", "AGENT_SERVE_PORT", _env_int),
]


class Config:
    """智能体配置：默认值 ← YAML（~/.agent/config.yaml）← 环境变量 ← CLI 覆盖。"""

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

        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                self._deep_merge(config, user_config)
                logger.info(f"Loaded config from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")

        # Environment variable overrides（第三层，声明式表见 _ENV_OVERRIDES）
        self._apply_env_overrides(config)

        # 沙箱配置：顶层 `sandbox:`（工具集工厂读取）与 `multi_agent.sandbox`
        # 合并，multi_agent.sandbox 优先（T025，contracts/config.md）。
        config["sandbox"] = self._merge_sandbox(config)

        return config

    # ---- 覆盖层辅助（第三层环境变量 + 第四层 CLI）----
    @staticmethod
    def _get_dotted(config: Dict[str, Any], path: str) -> Any:
        """按 `.` 分隔路径读取嵌套 dict 值（路径节点必须已存在）。"""
        node = config
        for part in path.split("."):
            node = node[part]
        return node

    @staticmethod
    def _set_dotted(config: Dict[str, Any], path: str, value: Any) -> None:
        """按 `.` 分隔路径写入嵌套 dict 值（路径节点必须已存在）。"""
        node = config
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

    @staticmethod
    def _apply_env_overrides(config: Dict[str, Any]) -> None:
        """应用环境变量覆盖层（第三层）：遍历 _ENV_OVERRIDES 声明式表。

        取每个配置点第一个已设置的环境变量（空串视为已设置），经转换器
        落到对应路径；未设置则跳过，保持默认/YAML 值。
        """
        for path, env_names, converter in _ENV_OVERRIDES:
            names = env_names if isinstance(env_names, tuple) else (env_names,)
            raw = next((os.getenv(n) for n in names if os.getenv(n) is not None), None)
            if raw is None:
                continue
            current = Config._get_dotted(config, path)
            Config._set_dotted(config, path, converter(raw, current))

    def apply_overrides(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        max_steps: Optional[int] = None,
        no_compaction: bool = False,
        hitl: Optional[str] = None,
        workspace_root: Optional[str] = None,
    ) -> None:
        """应用 CLI 覆盖层（最高优先级：默认值 ← YAML ← 环境变量 ← CLI）。

        仅在参数非空时覆盖对应配置（None/空串不覆盖，保持已有值）。
        """
        if api_key:
            self.data["llm"]["api_key"] = api_key
        if model:
            self.data["llm"]["model"] = model
        if base_url:
            self.data["llm"]["base_url"] = base_url
        if max_steps:
            self.data["agent"]["max_steps"] = max_steps
        if no_compaction:
            self.data["agent"]["enable_compaction"] = False
        if hitl:
            self.data["policy"]["hitl_mode"] = hitl
        if workspace_root:
            self.data["policy"]["workspace_root"] = workspace_root

    @staticmethod
    # 覆盖层核心：默认值 ← YAML 用户配置 ← 环境变量 ← CLI。
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """将 override 递归合并到 base 中。"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value

    def multi_agent_settings(self) -> Dict[str, Any]:
        """返回多Agent 配置段（始终存在默认值，缺失时自动补默认，向后兼容）。"""
        settings = self.data.get("multi_agent") or {}
        merged = dict(DEFAULT_CONFIG["multi_agent"])
        merged.update(settings)
        return merged

    @staticmethod
    def _merge_sandbox(config: Dict[str, Any]) -> Dict[str, Any]:
        """合并顶层 `sandbox:` 与 `multi_agent.sandbox`，后者优先（T025）。"""
        merged = dict(DEFAULT_CONFIG["sandbox"])
        merged.update(config.get("sandbox") or {})
        merged.update((config.get("multi_agent") or {}).get("sandbox") or {})
        return merged

    def sandbox_settings(self) -> Dict[str, Any]:
        """返回合并后的沙箱配置（multi_agent.sandbox 覆盖顶层 sandbox:）。"""
        return self._merge_sandbox(self.data)

    def create_llm(self) -> LLM:
        """根据配置创建 LLM provider。"""
        llm_config = self.data["llm"]
        return LiteLLMProvider(
            model=llm_config["model"],
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
        )

    # ---- 企业级安全策略装配（002-enterprise-cli-upgrade US1，contracts/config.md）----
    def policy_components(self):
        """从 `policy` 配置段构建守卫/HITL/审计组件。

        返回 ``(path_guard, command_guard, hitl_policy, audit_log)`` 四元组，
        供 create_tool_executor / create_tool_calling_llm 注入（US1 T013/T014）。
        audit_dir 为空 → 默认 ~/.agent/audit（audit.py 的 DEFAULT_AUDIT_DIR）。
        """
        policy = self.data.get("policy") or {}
        path_guard = PathGuard(workspace_root=policy.get("workspace_root", ""))
        command_guard = CommandGuard(
            command_blacklist=policy.get("command_blacklist", []),
            command_allowlist=policy.get("command_allowlist", []),
        )
        hitl_policy = HitlPolicy(mode=policy.get("hitl_mode", "auto"))
        audit_dir = policy.get("audit_dir") or ""
        audit_path = Path(audit_dir).expanduser() / "audit.jsonl" if audit_dir else None
        audit_log = AuditLog(path=audit_path)
        return path_guard, command_guard, hitl_policy, audit_log

    # ---- 企业级用量/成本装配（US2 T020/T021，contracts/config.md `cost:`）----
    def create_cost_estimator(self) -> CostEstimator:
        """从 `cost.pricing` 配置段构建成本估算器（空表 = 只记 token，R-06）。"""
        pricing = (self.data.get("cost") or {}).get("pricing", {}) or {}
        return CostEstimator(pricing=pricing)

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
        # 企业级守卫/审批/审计注入（US1 T013/T016，缺省 policy 段即默认行为）
        path_guard, command_guard, hitl_policy, audit_log = self.policy_components()
        return ToolExecutor(
            toolsets=all_toolsets,
            toolset_tag_filter=toolset_tag_filter,
            path_guard=path_guard,
            command_guard=command_guard,
            hitl_policy=hitl_policy,
            audit_log=audit_log,
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
        # 企业级 HITL 策略 + 审计（US1 T014/T016，/hitl 运行时切换依赖注入）
        _, _, hitl_policy, audit_log = self.policy_components()
        # 企业级用量/成本（US2 T020/T021，FR-007）：record_usage 开关 + 定价表
        cost_estimator = self.create_cost_estimator()
        return ToolCallingLLM(
            tool_executor=tool_executor
            or self.create_tool_executor(toolset_tag_filter=toolset_tag_filter),
            llm=llm or self.create_llm(),
            max_steps=agent_config["max_steps"],
            enable_compaction=agent_config.get("enable_compaction", True),
            compaction_threshold_ratio=agent_config.get(
                "compaction_threshold_ratio", 0.75
            ),
            compaction_keep_last_n=agent_config.get("compaction_keep_last_n", 6),
            hitl_policy=hitl_policy,
            audit_log=audit_log,
            cost_estimator=cost_estimator,
            record_usage=agent_config.get("record_usage", True),
        )


