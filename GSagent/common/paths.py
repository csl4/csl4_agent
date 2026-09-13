"""统一的项目内数据根目录（./.GSagent）。所有默认路径的单一来源。

本项目默认把配置/日志/数据全部落在当前工作目录下的 `./.GSagent`，
不写用户主目录。本模块是最底层（仅依赖 stdlib），`config.py` / `core/` /
`plugins/toolsets/bash/common/cli_prefixes.py` 都可引用它而不引入循环导入
（`cli_prefixes.py` 不能 import `GSagent.config`，但 import 本模块安全）。

注意：这些是模块级常量，import 时按 `Path.cwd()` 求值一次；运行期如需
显式覆盖，仍可用 `AGENT_*` 环境变量或 CLI 的 `--config-file`。
"""

from pathlib import Path

DEFAULT_AGENT_DIR = Path.cwd() / ".GSagent"

DEFAULT_CONFIG_FILE   = DEFAULT_AGENT_DIR / "config.yaml"
DEFAULT_LOG_DIR       = DEFAULT_AGENT_DIR / "logs"
DEFAULT_AUDIT_DIR     = DEFAULT_AGENT_DIR / "audit"
DEFAULT_SNAPSHOT_DIR  = DEFAULT_AGENT_DIR / "snapshots"
DEFAULT_MEMORY_DB     = DEFAULT_AGENT_DIR / "memory.db"
DEFAULT_SESSIONS_DIR  = DEFAULT_AGENT_DIR / "memories" / "sessions"
DEFAULT_RUNTIME_DB    = DEFAULT_AGENT_DIR / "runtime.db"
DEFAULT_HISTORY_FILE  = DEFAULT_AGENT_DIR / "history.jsonl"
DEFAULT_SKILLS_DIR    = DEFAULT_AGENT_DIR / "skills"
DEFAULT_PREFIXES_FILE = DEFAULT_AGENT_DIR / "bash_approved_prefixes.yaml"
