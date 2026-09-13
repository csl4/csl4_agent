"""破坏性命令守卫（002-enterprise-cli-upgrade US1，FR-002，R-03）。

在**审批前**拦截明显破坏性的系统命令（黑名单优先于审批，不可被
HITL=never 豁免，T013）：

- 内置危险正则集（rm -rf 绝对路径、mkfs、dd /dev/zero、fork bomb、
  chmod -R 777 /、shutdown/reboot、find /）；
- 可配置 ``command_blacklist`` 追加规则；
- ``command_allowlist`` 是**显式放行出口**（用户配置的硬豁免，优先级最高）。

与既有 bash 工具集的前缀校验层（validation.py 的 DENIED 语义）互补：
本守卫是更粗颗粒的「绝对拒绝」层，先于审批与工具内部校验执行。
"""

import re
from typing import List, Optional, Tuple

# 内置危险命令正则集（FR-002 语义）。覆盖常见破坏性命令的关键形态；
# 更细粒度的命令校验仍由 bash/sandbox 工具集自身的前缀白名单层完成。
BUILTIN_DANGEROUS_PATTERNS: Tuple[str, ...] = (
    # 1. rm -r -f 组合 + 绝对路径目标（删除根/系统/工作区外路径）
    #    合并标志（-rf / -fr）：
    r"\brm\b[^\n]*\s-[a-zA-Z]*[rf][a-zA-Z]*[rf][a-zA-Z]*\s+/(?:[^\s]*)",
    #    分离标志（-r -f / -f -r）：
    r"\brm\b[^\n]*(?:\s-[a-zA-Z]*[rf][a-zA-Z]*)+\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(?:[^\s]*)",
    # 2. mkfs / mkfs.ext4（格式化文件系统）
    r"\bmkfs(?:\.[a-zA-Z0-9]+)?\b",
    # 3. dd if=/dev/zero（零覆写磁盘/设备）
    r"\bdd\b[^\n]*\bif\s*=\s*/dev/zero\b",
    # 4. fork bomb :(){ :|:& };:
    r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:[^}]*\}",
    # 5. chmod -R 777 /（根目录全权）
    r"\bchmod\b[^\n]*\s-R\b[^\n]*\b777\b[^\n]*\s+/",
    # 6. shutdown / reboot / poweroff（halt 太常见词，不内置）
    r"\b(?:shutdown|reboot|poweroff)\b",
    # 7. find /（绝对路径整盘扫描/配合 -delete 删除；相对路径 find . 不拦）
    r"\bfind\s+/",
)


class CommandGuard:
    """按内置危险集 + 可配置黑名单拦截命令，白名单显式放行（R-03）。"""

    def __init__(
        self,
        command_blacklist: Optional[List[str]] = None,
        command_allowlist: Optional[List[str]] = None,
    ) -> None:
        # 内置集与黑名单都编译为搜索正则；白名单命中即放行（最高优先级）。
        self.builtin = [re.compile(p) for p in BUILTIN_DANGEROUS_PATTERNS]
        self.blacklist = [re.compile(p) for p in (command_blacklist or [])]
        self.allowlist = [re.compile(p) for p in (command_allowlist or [])]

    def check(self, command: str) -> Tuple[bool, str]:
        """校验命令是否命中危险集。

        返回 ``(ok, reason)``：``ok=False`` 时 ``reason`` 给出命中来源
        （内置/黑名单）；空命令返回 ``(True, "")`` 交由工具自身校验。
        """
        if not isinstance(command, str) or not command.strip():
            return True, ""
        for pattern in self.allowlist:
            if pattern.search(command):
                return True, ""
        for pattern in self.builtin:
            if pattern.search(command):
                return False, f"Command matches builtin dangerous pattern: {pattern.pattern}"
        for pattern in self.blacklist:
            if pattern.search(command):
                return False, f"Command matches blacklist pattern: {pattern.pattern}"
        return True, ""


__all__ = ["BUILTIN_DANGEROUS_PATTERNS", "CommandGuard"]
