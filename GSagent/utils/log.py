"""agent 的日志配置。"""

# ======================= 中文导览 =======================
# 日志装配：setup_logging(level, format) 配置进程级日志。
#   · stderr handler（StreamHandler）+ 文件 handler（RotatingFileHandler，
#     默认 ./.GSagent/logs/agent.log，5MB×5 轮转，UTF-8 编码）——handler 挂在
#     【根 logger】上，这样 openai/httpx 等第三方日志也能落盘（它们与 agent 是
#     兄弟命名空间，挂 GSagent 上永远进不了文件）。
#   · 幂等装配：模块级哨兵 handler 引用 + 成员检查，重复调用不产生重复 handler；
#     绝不 clear() 根 handler，嵌入方自己的 handler 原样保留。
#   · 级别：GSagent.* 由 level 参数/AGENT_LOG_LEVEL 控制；受管第三方 logger 统一
#     setLevel（AGENT_LOG_THIRD_PARTY_LEVEL，默认 WARNING）；根 logger 级别不主动
#     改动（最小侵入）。
#   · 可选 excepthook：install_excepthook=True 时把未捕获异常写进日志文件，
#     并链式调用前一 hook（不吞异常、终端回溯照常、退出码不变）。
#   · Windows：文件 handler 必须 encoding="utf-8"，否则中文环境(cp936)乱码。
# =========================================================

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Optional

# log_dir 参数与 env 访问函数同名，import 加别名避免遮蔽。
from GSagent.common import (
    DEFAULT_LOG_DIR,
    LOG_LEVEL,
    log_file_enabled,
    third_party_log_level,
)
from GSagent.common import (
    log_dir as env_log_dir,
)

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
# 与统一数据根目录对齐（common/paths.py：./.GSagent/logs）。
DEFAULT_LOG_FILE_NAME = "agent.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # ~5MB
DEFAULT_BACKUP_COUNT = 5

# 幂等装配的状态（模块级）。
_INSTALLED_STDERR_HANDLER: Optional[logging.Handler] = None
_INSTALLED_FILE_HANDLER: Optional[logging.Handler] = None
_EXCEPTHOOK_INSTALLED = False
_PREVIOUS_EXCEPTHOOK: Optional[Callable[..., Any]] = None

# 受管第三方 logger（002-langchain-ecosystem 后 litellm 已移除），默认 WARNING。
_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
    "h11",
    "h2",
    "urllib3",
    "openai",
    "tenacity",
    "filelock",
    "asyncio",
)


def _configure_third_party() -> None:
    """按需设置受管第三方 logger 的级别。"""
    default = third_party_log_level()
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(getattr(logging, default, logging.WARNING))


def setup_logging(
    level: str | None = None,
    format_string: str | None = None,
    *,
    log_dir: str | Path | None = None,  # 测试注入缝；None → env → 默认
    enable_file_logging: bool | None = None,  # 测试注入缝；None → env
    install_excepthook: bool = False,
) -> Optional[Path]:
    """配置 agent 的进程级日志：stderr + 可选文件日志。

    参数:
        level: 日志级别（DEBUG、INFO、WARNING、ERROR、CRITICAL）。默认取 AGENT_LOG_LEVEL 环境变量。
        format_string: 自定义日志格式。默认 ISO 时间戳 + 级别 + 日志器 + 消息。
        log_dir: 文件日志目录。None 时取 AGENT_LOG_DIR，再默认 ./.GSagent/logs。
        enable_file_logging: 是否开启文件日志。None 时取 AGENT_LOG_FILE（默认开启）。
        install_excepthook: 把未捕获异常写入日志文件，并链式调用前一 hook。

    返回:
        文件日志路径；文件日志关闭或创建失败时为 None。
    """
    global _INSTALLED_STDERR_HANDLER, _INSTALLED_FILE_HANDLER
    global _EXCEPTHOOK_INSTALLED, _PREVIOUS_EXCEPTHOOK
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    if level is None:
        level = LOG_LEVEL
    if format_string is None:
        format_string = DEFAULT_LOG_FORMAT

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    formatter = logging.Formatter(format_string)
    root = logging.getLogger()

    # ---- stderr handler（幂等）----
    if _INSTALLED_STDERR_HANDLER is None or _INSTALLED_STDERR_HANDLER not in root.handlers:
        _INSTALLED_STDERR_HANDLER = logging.StreamHandler(sys.stderr)
    _INSTALLED_STDERR_HANDLER.setFormatter(formatter)
    if _INSTALLED_STDERR_HANDLER not in root.handlers:
        root.addHandler(_INSTALLED_STDERR_HANDLER)

    # ---- 文件 handler（可选、幂等、容错）----
    file_path: Optional[Path] = None
    enable = enable_file_logging if enable_file_logging is not None else log_file_enabled()
    if enable:
        if _INSTALLED_FILE_HANDLER is None or _INSTALLED_FILE_HANDLER not in root.handlers:
            if log_dir is not None:
                target = Path(log_dir)
            elif env_log_dir():
                target = Path(env_log_dir())
            else:
                target = DEFAULT_LOG_DIR
            file_path = target / DEFAULT_LOG_FILE_NAME
            try:
                target.mkdir(parents=True, exist_ok=True)
                _INSTALLED_FILE_HANDLER = logging.handlers.RotatingFileHandler(
                    file_path,
                    maxBytes=DEFAULT_MAX_BYTES,
                    backupCount=DEFAULT_BACKUP_COUNT,
                    encoding="utf-8",  # Windows 中文环境关键
                )
            except OSError:
                # 目录不可写（只读 home / CI）时降级为仅 stderr，不抛异常。
                print(
                    f"警告：无法创建日志文件 {file_path}，已降级为仅 stderr",
                    file=sys.stderr,
                )
                _INSTALLED_FILE_HANDLER = None
                file_path = None
        if _INSTALLED_FILE_HANDLER is not None:
            file_path = Path(_INSTALLED_FILE_HANDLER.baseFilename)
            _INSTALLED_FILE_HANDLER.setFormatter(formatter)
            if _INSTALLED_FILE_HANDLER not in root.handlers:
                root.addHandler(_INSTALLED_FILE_HANDLER)
    else:
        # 运行期关闭：卸载并关闭旧文件 handler，支持再次开启时重建。
        if _INSTALLED_FILE_HANDLER is not None:
            if _INSTALLED_FILE_HANDLER in root.handlers:
                root.removeHandler(_INSTALLED_FILE_HANDLER)
            _INSTALLED_FILE_HANDLER.close()
            _INSTALLED_FILE_HANDLER = None

    # ---- 级别：agent.* 由 level 参数控制；根级别不主动改动（最小侵入）----
    logging.getLogger("agent").setLevel(numeric)

    # ---- 第三方 logger 级别管理 ----
    _configure_third_party()

    # ---- 可选 excepthook（链式调用前一 hook）----
    if install_excepthook and not _EXCEPTHOOK_INSTALLED:
        _EXCEPTHOOK_INSTALLED = True
        _PREVIOUS_EXCEPTHOOK = sys.excepthook

        def _hook(
            exc_type: type[BaseException],
            exc_value: BaseException,
            exc_tb: Optional[TracebackType],
        ) -> None:
            logging.getLogger("agent").critical(
                "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
            )
            if _PREVIOUS_EXCEPTHOOK is not None:
                _PREVIOUS_EXCEPTHOOK(exc_type, exc_value, exc_tb)

        sys.excepthook = _hook

    return file_path


def _reset_logging() -> None:
    """测试专用：卸载本模块安装的 handler，恢复初始状态（含 excepthook）。

    直接回退到解释器默认 sys.__excepthook__，避免链式引用把测试内的局部
    函数残留成全局状态（pytest 的 monkeypatch teardown 先于本函数执行）。
    """
    global _INSTALLED_STDERR_HANDLER, _INSTALLED_FILE_HANDLER
    global _EXCEPTHOOK_INSTALLED, _PREVIOUS_EXCEPTHOOK
    root = logging.getLogger()
    for h in (_INSTALLED_STDERR_HANDLER, _INSTALLED_FILE_HANDLER):
        if h is not None and h in root.handlers:
            root.removeHandler(h)
            h.close()
    _INSTALLED_STDERR_HANDLER = None
    _INSTALLED_FILE_HANDLER = None
    if _EXCEPTHOOK_INSTALLED:
        sys.excepthook = sys.__excepthook__
    _EXCEPTHOOK_INSTALLED = False
    _PREVIOUS_EXCEPTHOOK = None
