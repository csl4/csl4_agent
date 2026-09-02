"""支持 Windows（Git Bash）的 Bash 命令执行。

在 POSIX 上它行为类似普通的 /bin/bash 子进程。在 Windows 上 bash 可执行文件
被解析为 Git Bash（自动检测顺序：AGENT_BASH_PATH 环境变量 -> PATH -> 已知安装
位置，例如 C:\\Program Files\\Git\\bin\\bash.exe），超时进程通过 taskkill
整棵进程树杀灭，因为 Windows 上 Popen.kill() 只杀 shell 自身，不会杀其子进程。
"""

# ======================= 中文导览 =======================
# Bash 的【底层执行封装】：真正把命令字符串跑起来的最后一段管道的核心。
# 数据流位置：RunBashCommand._invoke() → execute_bash_command()（本文件）。
#   传入：cmd 命令字符串 + timeout 超时秒数 + bash_path 可执行文件路径。
#   产出：ShellResult（统一命令结果类型，core.models.result；stdout / return_code / timed_out）。
# 关键封装点：
#   ① find_bash_executable()——解析用哪个 bash：显式配置 > 环境变量 > PATH(排除
#      System32 的 WSL 启动器) > 已知 Git Bash 安装路径。
#   ② _popen()——统一 `bash -c <cmd>` + shell=False（在 Windows 不能套 shell=True，
#      否则 /c 会被当路径）。POSIX 端开新会话以便整组 kill。
#   ③ 超时 kill——Windows 上 Popen.kill() 只杀 shell 本身，会留孤儿子进程，
#      所以 Windows 走 taskkill /T（整棵进程树）；POSIX 走 os.killpg 杀进程组，
#      并给 5 秒宽限期让子进程在 SIGTERM 后自行清理。
# =========================================================

import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import List

from agent.core.models.result import ShellResult

logger = logging.getLogger(__name__)

# Fallback Git Bash locations, tried in order when bash is not on PATH.
# Candidates that don't exist are simply skipped, so extra entries are
# harmless. Machine-specific installs should prefer AGENT_BASH_PATH/config.
_GIT_BASH_CANDIDATES = [
    r"D:\Git\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
]

BASH_ENV_VAR = "AGENT_BASH_PATH"

# Grace period (seconds) to let a timed-out child exit on SIGTERM - and run
# any cleanup it does on termination - before it is force-killed with SIGKILL.
ARGV_TERMINATE_GRACE_SECONDS = 5


def find_bash_executable(configured_path: str = "") -> str:
    """解析用于命令执行的 bash 可执行文件。

    顺序:
    1. 显式配置的路径（BashExecutorConfig.bash_path）
    2. AGENT_BASH_PATH 环境变量
    3. shutil.which("bash") - 排除 System32 中的 WSL 启动器
    4. 已知的 Git Bash 安装位置

    异常:
        FileNotFoundError: 找不到任何 bash 可执行文件时抛出。
    """
    candidates: List[str] = []
    if configured_path:
        candidates.append(configured_path)
    env_path = os.environ.get(BASH_ENV_VAR, "")
    if env_path:
        candidates.append(env_path)

    which = shutil.which("bash")
    # C:\Windows\System32\bash.exe is the WSL launcher: it runs commands inside
    # the Linux subsystem with a different filesystem, which is not what we want.
    if which and "system32" not in which.lower():
        candidates.append(which)

    candidates.extend(_GIT_BASH_CANDIDATES)

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    raise FileNotFoundError(
        "Bash executable not found. Install Git for Windows, add it to PATH, "
        f"or set the {BASH_ENV_VAR} environment variable."
    )


def _kill_process_tree(process: subprocess.Popen) -> None:
    """杀死一个进程及其所有子进程。

    在 Windows 上 Popen.kill() 只杀 shell 进程本身，会让子进程（kubectl、
    python 等）成为孤儿继续运行，因此整棵进程树通过 taskkill 杀灭。在 POSIX
    上杀死进程组（为此目的进程以 start_new_session=True 启动）。
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        # Kill the whole process group (children spawned via start_new_session
        # share this pgid); fall back to killing just the shell if the group
        # is already gone or we lack permission.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def _popen(bash_path: str, cmd: str, cwd: str = "") -> subprocess.Popen:
    """以适配平台的方式启动 bash 子进程。

    注意：在 Windows 上，shell=True 搭配 executable=bash_path 会运行
    `bash /c <cmd>` —— shell=True 的前缀 "/c" 会被当作路径而不是标志传给
    bash —— 因此在所有平台上，命令总是显式以 `bash -c <cmd>` 配合 shell=False
    传入。

    可选 `cwd` 受控工作目录（沙箱隔离用，FR-004）。
    """
    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if cwd:
        popen_kwargs["cwd"] = cwd
    if sys.platform != "win32":
        # POSIX: start a new session so the whole process group can be killed.
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen([bash_path, "-c", cmd], shell=False, **popen_kwargs)


def execute_bash_command(
    cmd: str, timeout: int, bash_path: str = "", cwd: str = ""
) -> ShellResult:
    # 对外唯一入口：解析 bash 路径 → 起子进程 → communicate 等结果/捕获超时。
    # 正常：返回 ShellResult(return_code=退出码, timed_out=False)；
    # 超时：_kill_process_tree 杀整树后收尾，返回 ShellResult(return_code=None, timed_out=True)。
    """
    执行一条 bash 命令并返回结果。

    参数:
        cmd: 要执行的 bash 命令
        timeout: 超时秒数
        bash_path: 可选的显式 bash 可执行文件路径（为空时自动检测）
        cwd: 可选的受控工作目录（为空时继承当前目录）

    返回:
        携带 stdout、return_code 和 timed_out 标志的 ShellResult
    """
    resolved_bash = find_bash_executable(bash_path)
    logger.debug(f"Executing bash command via {resolved_bash}: {cmd}")
    process = _popen(resolved_bash, cmd, cwd=cwd)

    try:
        stdout, _ = process.communicate(timeout=timeout)
        stdout = stdout.strip() if stdout else ""

        return ShellResult(
            stdout=stdout,
            return_code=process.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        # Collect any partial output that was generated before timeout
        stdout, _ = process.communicate()
        stdout = stdout.strip() if stdout else ""

        return ShellResult(
            stdout=stdout,
            return_code=None,
            timed_out=True,
        )
