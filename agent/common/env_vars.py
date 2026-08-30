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
# Used by agent.utils.log at import time, before Config is available.
LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO")
