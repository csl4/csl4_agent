"""bash 工具集（langchain @tool 版，002-langchain-ecosystem）。

复用既有执行内核（``execute_bash_command``）与校验层（``validate_command``）：
- 命令校验：DENIED → 返回 ``{"__denied__": reason}``（硬拒绝，编排 ERROR）；
  APPROVAL_REQUIRED → 返回 ``{"__approval_required__": {...}}``（编排 interrupt 审批）；
  ALLOWED → 执行并返回 stdout。
- 动态审批：``validate_command`` 按命令内容判定（非静态规则）。
- 已批准前缀：``get_approved_prefixes`` 回调（registry.approved_prefixes），
  审批恢复后并入 allow 校验，使已批准命令重新执行时放行。
"""

from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool

from GSagent.plugins.toolsets.bash.common.bash import (
    execute_bash_command,
    find_bash_executable,
)
from GSagent.plugins.toolsets.bash.common.config import BashExecutorConfig
from GSagent.plugins.toolsets.bash.validation import (
    ValidationStatus,
    get_effective_lists,
    validate_command,
)


def create_bash_tools(
    config: Optional[Dict[str, Any]] = None,
    get_approved_prefixes: Optional[Callable[[], List[str]]] = None,
) -> List[Any]:
    """工厂：返回 bash @tool 列表（配置 + 已批准前缀回调闭包注入）。"""
    cfg = BashExecutorConfig(**(config or {}))
    allow_list, deny_list = get_effective_lists(cfg)
    bash_path = find_bash_executable(cfg.bash_path)

    @tool
    def bash(command: str, suggested_prefixes: List[str], timeout: int = 30) -> Any:
        """Execute a bash command with prefix-based safety validation.

        - ``command``: the bash command to run.
        - ``suggested_prefixes``: one expected prefix per command segment.
        - ``timeout``: seconds (default 30).
        """
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

        sr = execute_bash_command(command, timeout, bash_path)
        if sr.timed_out:
            return f"Error: Command timed out after {timeout}s."
        if sr.return_code != 0:
            return f"Error: Command exited with {sr.return_code}.\n{sr.stdout}"
        return sr.stdout or "No output."

    return [bash]


__all__ = ["create_bash_tools"]
