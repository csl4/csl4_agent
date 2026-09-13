"""基于前缀校验的 bash 工具集配置。"""

from typing import List, Literal

from pydantic import Field

from GSagent.utils.pydantic_utils import ToolsetConfig

# ======================= 中文导览 =======================
# Bash 工具集的【配置值对象】：装着命令审批所需的全部规则，由 create_bash_toolset()
#   把 config.yaml 的 `bash:` 段灌进来。
# HARDCODED_BLOCKS：硬编码黑名单（sudo/su）——【永远拦截、不可被审批豁免】，
#   这是兜底安全阀，与用户可配置的 deny 黑名单不同。
# BashExecutorConfig（继承 ToolsetConfig）：
#   allow/deny  → 用户自己追加的白/黑名单前缀。
#   builtin_allowlist → 选内置白名单档位 none/core/extended（见 default_lists.py）。
#   bash_path   → 用哪个 bash；空则自动探测（Windows 上 Git Bash）。
# 消费方：validation.py 的 get_effective_lists() 读取它算出生效名单。
# =========================================================

# Hardcoded blocks - these patterns are ALWAYS blocked and cannot be overridden
HARDCODED_BLOCKS: List[str] = [
    "sudo",
    "su",
]


# ---- 行为对象的配置（盒子）：装着 bash 审批规则的配置对象 ----
# 由 create_bash_toolset() 实例化并挂到 Toolset.config；工具代码 via
# bash_toolset._get_config() 取出（运行时 READ 用）。字段含义见各字段注释。
class BashExecutorConfig(ToolsetConfig):
    """基于前缀校验的 bash 工具集配置。"""

    # Allow/deny lists for prefix-based command validation
    allow: List[str] = Field(
        default_factory=list,
        title="Allow List",
        description="Additional command prefixes to allow (merged with builtin allowlist)",
    )
    deny: List[str] = Field(
        default_factory=list,
        title="Deny List",
        description="Command prefixes to deny (takes precedence over allow list)",
    )

    # Controls which builtin allowlist to use:
    # - "core": text processing, system info (safe everywhere)
    # - "extended": core + filesystem commands (cat, find, ls, base64)
    # - "none": empty builtin list, user manages their own via `allow`
    builtin_allowlist: Literal["none", "core", "extended"] = Field(
        default="core",
        title="Builtin Allowlist",
        description='Which builtin allowlist to include: "none", "core", or "extended"',
    )

    # Path to the bash executable. Empty string means auto-detect
    # (Git Bash on Windows, /bin/bash on POSIX).
    bash_path: str = Field(
        default="",
        title="Bash Path",
        description="Path to the bash executable (empty = auto-detect: Git Bash on Windows)",
    )
