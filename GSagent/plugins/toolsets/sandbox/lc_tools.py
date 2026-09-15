"""sandbox 工具集（langchain @tool 版，002-langchain-ecosystem）。

复用 bash 校验层（``validate_command``）与跨终端执行（``execute_in_shell``）：
- 动态审批：DENIED → ``{"__denied__": ...}``；APPROVAL_REQUIRED → ``{"__approval_required__": ...}``；
  ALLOWED → ``execute_in_shell`` 跨终端执行（探测 + 同义改写 + 超时整树杀灭）。
- 类型检查：v1 仅 ``lightweight``；``container`` 明确报错（不静默降级）。
- 守卫：工具名 "sandbox" 命中 COMMAND_TOOLS，注册时自动挂 CommandGuard。
"""

from typing import Any, Callable, Dict, List, Literal, Optional

from langchain_core.tools import tool
from pydantic import Field

from GSagent.core.env.terminal import execute_in_shell
from GSagent.plugins.toolsets.bash.validation import (
    ValidationStatus,
    get_effective_lists,
    validate_command,
)
from GSagent.utils.pydantic_utils import ToolsetConfig


class SandboxExecutorConfig(ToolsetConfig):
    """轻量沙箱工具集配置（extra="allow"，宪法 III 向后兼容）。

    字段与 BashExecutorConfig 对齐（builtin_allowlist/allow/deny 复用 bash
    校验层的生效名单语义）。v1 仅支持 lightweight；container 为 v2 占位 →
    明确报错（FR-004，不静默降级）。
    """

    type: str = Field(
        default="lightweight",
        title="Sandbox Type",
        description='Sandbox type: "lightweight" (v1) or "container" (v2, not implemented)',
    )
    timeout_seconds: int = Field(
        default=30,
        title="Timeout Seconds",
        description="Default command timeout in seconds.",
    )
    working_dir: str = Field(
        default="",
        title="Working Directory",
        description="Controlled working directory for sandboxed commands (empty = inherit cwd).",
    )

    # 复用 bash 校验层的白/黑名单字段（与 BashExecutorConfig 同名同语义）。
    allow: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=list)
    builtin_allowlist: Literal["none", "core", "extended"] = Field(default="extended")

    bash_path: str = Field(
        default="",
        title="Bash Path",
        description="Path to bash executable (empty = auto-detect, Git Bash on Windows).",
    )


def create_sandbox_tools(
    config: Optional[Dict[str, Any]] = None,
    get_approved_prefixes: Optional[Callable[[], List[str]]] = None,
) -> List[Any]:
    """工厂：返回 sandbox @tool 列表（配置 + 已批准前缀回调闭包注入）。"""
    cfg = SandboxExecutorConfig(**(config or {}))
    allow_list, deny_list = get_effective_lists(cfg)
    bash_path = cfg.bash_path

    @tool
    def sandbox(
        command: str,
        suggested_prefixes: List[str],
        timeout: Optional[int] = None,
        cwd: str = "",
    ) -> Any:
        """Execute a command in a controlled lightweight sandbox (cross-terminal).

        - ``command``: the command to run.
        - ``suggested_prefixes``: one expected prefix per command segment.
        - ``timeout``: seconds (default config timeout_seconds).
        - ``cwd``: working directory (default config working_dir).
        """
        if cfg.type != "lightweight":
            return f"Error: Sandbox type '{cfg.type}' is not supported (v1 = lightweight)."
        eff_timeout = timeout or cfg.timeout_seconds
        eff_cwd = cwd or cfg.working_dir

        approved = list(get_approved_prefixes() or []) if get_approved_prefixes else []
        effective_allow = sorted(set(allow_list + approved))

        result = validate_command(command, suggested_prefixes, effective_allow, deny_list)
        if result.status == ValidationStatus.DENIED:
            return {"__denied__": result.message or "command denied"}
        if result.status == ValidationStatus.APPROVAL_REQUIRED:
            return {
                "__approval_required__": {
                    "reason": result.message or "command requires approval",
                    "prefixes_to_save": result.prefixes_needing_approval
                    or suggested_prefixes,
                }
            }

        sr = execute_in_shell(
            command, shell=None, timeout=eff_timeout, cwd=eff_cwd, bash_path=bash_path
        )
        if sr.timed_out:
            return f"Error: Command timed out after {eff_timeout}s."
        if sr.return_code != 0:
            return f"Error: Command exited with {sr.return_code}.\n{sr.stdout}"
        return sr.stdout or "No output."

    return [sandbox]


__all__ = ["create_sandbox_tools"]
