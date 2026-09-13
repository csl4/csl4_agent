"""工作区路径守卫（002-enterprise-cli-upgrade US1，FR-001，R-04）。

限定路径类工具访问在 `workspace_root` 内（真实路径语义）：

- 相对路径按根目录拼接后经 ``realpath`` 解析；
- 绝对路径直接 ``realpath`` 解析；
- ``..`` 与 symlink 逃逸经 ``realpath`` 自然归一化后即可识别并拒绝。

`workspace_root` 为空时以**运行时当前目录**为根（contracts/config.md），
即默认把文件访问限定在启动目录内——与既有 filesystem 工具集
``root_dir="."`` 的沙箱语义一致，只是收敛到统一策略入口。

守卫是「审批前、永不豁免」的硬门（T013）：即使 HITL=never 强制放行，
越界路径依然被拦截。
"""

import os
from pathlib import Path
from typing import Tuple


class PathGuard:
    """以真实路径解析判定路径是否落在工作区根内的守卫。"""

    def __init__(self, workspace_root: str = "") -> None:
        # 空串 = 运行时当前目录；在 check() 时惰性解析，尊重启动后的 cwd。
        self.workspace_root = workspace_root

    def _root(self) -> Path:
        raw = (
            Path(self.workspace_root).expanduser()
            if self.workspace_root
            else Path.cwd()
        )
        return raw.resolve()

    def check(self, path: str) -> Tuple[bool, str]:
        """校验路径是否落在真实根目录内。

        返回 ``(ok, reason)``：``ok=False`` 时 ``reason`` 描述越界原因。
        空路径/非字符串返回 ``(True, "")``——必填校验交由工具自身处理，
        守卫只对「可解析的真实路径」负责。
        """
        if not isinstance(path, str) or not path.strip():
            return True, ""
        root = self._root()
        raw = Path(path).expanduser()
        target = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        if target != root and root not in target.parents:
            return False, (
                f"Path '{path}' resolves outside the workspace root '{root}'. "
                "Access denied."
            )
        return True, ""


__all__ = ["PathGuard"]
