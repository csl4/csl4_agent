"""运行时环境变量。

所有 agent 配置（模型、API key、base URL、max steps、工具结果
目录、服务端设置）都由 `agent.config.Config` 负责——请勿在此重复声明
这些常量；重复的默认值会漂移失联。

本模块只放置那些在 Config 中没有归属的 import 期常量。
"""

import os

# ======================= 中文导览 =======================
# 运行时环境变量（仅 import 期常量）。
# 铁律：模型/API key/base URL/max steps/tool 结果目录/服务端设置这些配置【全归
#   agent.config.Config】——不要在这里重复声明，否则两份默认值会漂移失联。
# 这里只放「不能等 Config」的 import 期常量：LOG_LEVEL 在 agent.utils.log
#   import 时就已被读取（那时 Config 还没构造）。
# =========================================================

# --- Logging ---
# LOG_LEVEL 在 agent.utils.log import 期就被读取（那时 Config 还没构造），保持冻结常量。
LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO")

# 以下三个仅在 setup_logging() 运行期读取，按「使用时读取」做成函数而不是冻结常量，
# 这样运行期修改 os.environ 能生效，测试也能直接 monkeypatch（参见 CODE_REVIEW P2-6）。
def log_file_enabled() -> bool:
    """文件日志开关：AGENT_LOG_FILE 非 0/false/no/off 即开启（默认开启）。"""
    return os.getenv("AGENT_LOG_FILE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def log_dir() -> str:
    """日志目录：AGENT_LOG_DIR 覆盖；空串表示用默认 ~/.agent/logs。"""
    return os.getenv("AGENT_LOG_DIR", "").strip()


def third_party_log_level() -> str:
    """受管第三方 logger 的统一级别（默认 WARNING；LiteLLM 固定 ERROR）。"""
    return os.getenv("AGENT_LOG_THIRD_PARTY_LEVEL", "WARNING").strip().upper()
