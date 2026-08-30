"""agent 的日志配置。"""

# ======================= 中文导览 =======================
# 日志装配：setup_logging(level, format) 配置 `agent` 根日志器。
# 主循环内部用 logger.info/debug 打点（命令执行、工具调用等），由这里统一格式化到 stderr。
# 关键前置动作（须在首次 import litellm 前）：
#   · 设 LITELLM_LOCAL_MODEL_COST_MAP=True（跳过远端模型成本表、离线友好）。
#   · 把 LiteLLM 日志压到 ERROR（压掉无害的 WARNING 降级提示）。
# 默认级别取 agent.common.LOG_LEVEL（环境变量 AGENT_LOG_LEVEL），可被 level 参数覆盖。
# =========================================================

import logging
import os
import sys

from agent.common import LOG_LEVEL


def setup_logging(level: str | None = None, format_string: str | None = None) -> None:
    """配置 agent 的根日志器。

    参数:
        level: 日志级别（DEBUG、INFO、WARNING、ERROR、CRITICAL）。默认取 AGENT_LOG_LEVEL 环境变量。
        format_string: 自定义日志格式。默认为 ISO 时间戳 + 级别 + 日志器 + 消息。
    """
    # Skip LiteLLM's remote model-cost-map fetch entirely (offline-friendly) and
    # silence its harmless WARNING-level fallback notices. Must run before the
    # first `import litellm`, which reads this env var at import time.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)

    if level is None:
        level = LOG_LEVEL

    if format_string is None:
        format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(format_string))

    root = logging.getLogger("agent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)