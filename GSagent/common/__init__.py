"""通用常量和环境变量。

注意：所有 agent 配置都位于 `GSagent.config.Config` 中。
本包只重新导出 import 期常量（例如 LOG_LEVEL）与统一的数据根目录常量
（`./.GSagent`，见 paths.py）。
"""

from GSagent.common.env_vars import (
    LOG_LEVEL,
    log_dir,
    log_file_enabled,
    third_party_log_level,
)
from GSagent.common.paths import (
    DEFAULT_AGENT_DIR,
    DEFAULT_AUDIT_DIR,
    DEFAULT_CONFIG_FILE,
    DEFAULT_HISTORY_FILE,
    DEFAULT_LOG_DIR,
    DEFAULT_MEMORY_DB,
    DEFAULT_PREFIXES_FILE,
    DEFAULT_RUNTIME_DB,
    DEFAULT_SESSIONS_DIR,
    DEFAULT_SKILLS_DIR,
    DEFAULT_SNAPSHOT_DIR,
)

__all__ = [
    "DEFAULT_AGENT_DIR",
    "DEFAULT_AUDIT_DIR",
    "DEFAULT_CONFIG_FILE",
    "DEFAULT_HISTORY_FILE",
    "DEFAULT_LOG_DIR",
    "DEFAULT_MEMORY_DB",
    "DEFAULT_PREFIXES_FILE",
    "DEFAULT_RUNTIME_DB",
    "DEFAULT_SESSIONS_DIR",
    "DEFAULT_SKILLS_DIR",
    "DEFAULT_SNAPSHOT_DIR",
    "LOG_LEVEL",
    "log_dir",
    "log_file_enabled",
    "third_party_log_level",
]
