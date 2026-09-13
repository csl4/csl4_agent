"""用户身份解析与目录编码（004-memory-isolation）。

记忆隔离的用户维度（specs/004-memory-isolation，contracts/user-key.md）：
- `resolve_user_key()`：多用户记忆隔离的身份 key——CLI 默认系统登录用户名，
  可被显式参数 / env / 配置覆盖；serve 模式 API 身份为预留扩展点。
- `safe_user_dir()`：用户 key → 安全目录名（防路径穿越与特殊字符），
  用于会话记忆目录 `<root>/<user>/<session_id>/` 层级。
"""

import getpass
import re
from typing import Optional

# 目录名允许字符：Unicode 词字符（\w 含中文/字母数字/下划线）+ 点/连字符；
# 其余（空格、/、\\、冒号、引号等路径危险字符）替换为 _
_SAFE_DIR_RE = re.compile(r"[^\w.-]")
# 拒绝的目录名（空 / 当前目录 / 父目录）
_FORBIDDEN_DIR = {"", ".", ".."}


def resolve_user_key(
    cfg_user: Optional[str] = None,
    identity: Optional[str] = None,
) -> str:
    """解析用户身份 key（记忆隔离维度）。

    来源优先级（高→低）：
    1. `identity`（serve API 客户端身份，**本期预留**，CLI 不传）
    2. 显式参数 `cfg_user`（调用方指定，测试/CI/服务化）
    3. `AGENT_MEMORY_USER` env 与 `memory.user` 配置——已在 config 层合并为 `cfg_user`
    4. 系统登录用户名（`getpass.getuser()`，默认）
    5. `"local"` 占位（getpass 失败/无 USER 环境，确定性回退，绝不抛错）

    Args:
        cfg_user: 显式配置用户（空/None 走下一步）。
        identity: serve 模式 API 身份（预留扩展点；非 None 时最高优先）。

    Returns:
        非空用户 key（原始字符，SQL 层直接使用；目录名用 `safe_user_dir` 编码）。
    """
    if identity:
        return identity
    if cfg_user and str(cfg_user).strip():
        return str(cfg_user).strip()
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - 无 USER/USERNAME 环境等，确定性回退
        return "local"


def safe_user_dir(user: str) -> str:
    """用户 key → 安全目录名：非 `[A-Za-z0-9._-]` 字符替换为 `_`。

    拒绝空 / `.` / `..`（防路径穿越）。编码仅用于目录路径；
    SQLite 的 `user` 列存 `resolve_user_key()` 原始返回值。
    """
    encoded = _SAFE_DIR_RE.sub("_", user or "")
    if encoded in _FORBIDDEN_DIR:
        return "_local"
    return encoded


__all__ = ["resolve_user_key", "safe_user_dir"]
