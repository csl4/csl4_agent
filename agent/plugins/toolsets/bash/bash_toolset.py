"""
带基于前缀的命令校验的 bash 工具集。

本工具集支持通过动态白名单执行 bash 命令。
命令使用前缀匹配对照 allow/deny 列表进行校验。
移植自 holmesgpt 的 bash 工具集，并针对 Windows(Git Bash)做了适配。
"""

# ======================= 中文导览 =======================
# Bash 工具集：以【前缀动态白名单】执行 bash 命令。
#   RunBashCommand（本工具集的唯一工具）：
#     - 覆写了 requires_approval()——按「命令段前缀」判断要否审批（危险/未知命令才要），
#       区别于基类按工具名通配符的默认判断。
#     - _invoke() 真正执行命令（execute_bash_command），并把结果 ShellResult 经
#       shell_result_to_structured 转成 StructuredToolResult。
#   安全分层：前缀命中白名单 → 直跑；未命中 → 需人工审批（并可记住前缀供后续免批）；
#             命中黑名单/硬编码块/敏感路径/危险参数 → 一律拒绝且不可被审批豁免。
# create_bash_toolset() 是工厂，tags=[CLI]，config 段走 `bash:`。
# =========================================================

import logging
from typing import Any, Dict, List, Optional

from pydantic import Field

from agent.core.models import (
    ApprovalRequirement,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
    shell_result_to_structured,
)
from agent.core.tools import (
    Tool,
    Toolset,
    ToolsetTag,
    ToolsetType,
    Transformer,
)
from agent.core.transformers import LineCountTransformer
from agent.plugins.toolsets.bash.common.bash import execute_bash_command
from agent.plugins.toolsets.bash.common.cli_prefixes import (
    load_cli_bash_tools_approved_prefixes,
)
from agent.plugins.toolsets.bash.common.config import BashExecutorConfig
from agent.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    get_effective_lists,
    validate_command,
)

logger = logging.getLogger(__name__)


def _get_config(context: ToolInvokeContext) -> BashExecutorConfig:
    """从调用上下文中获取工具集配置。"""
    toolset = getattr(context, "toolset", None)
    if toolset is not None and toolset.config is not None:
        return toolset.config
    return BashExecutorConfig()


def _merge_cli_approved_prefixes(context: ToolInvokeContext) -> None:
    """将 ~/.agent/bash_approved_prefixes.yaml 中的 CLI 已批准前缀
    合并到工具集的 allow 列表中。"""
    toolset = getattr(context, "toolset", None)
    config = getattr(toolset, "config", None)
    cli_prefixes = load_cli_bash_tools_approved_prefixes()
    if cli_prefixes and config is not None:
        # Build new list instead of mutating (preserves order, dedupes)
        merged = list(dict.fromkeys(config.allow + cli_prefixes))
        config.allow = merged
        logger.debug(f"Merged {len(cli_prefixes)} CLI-approved prefixes")


# ---- 行为对象：bash 命令执行工具（前缀校验 + 动态审批）----
# 输入：command(suggested_prefixes timeout)；输出：StructuredToolResult。
# 设计要点：
#   ① 动态审批——requires_approval() 在 invoke() 的步骤①被调用；命中需批则返回
#      ApprovalRequirement，主循环暂停让用户决断；ALLOWED 命令则免批直跑。
#   ② user_approved=True 时跳过错，直接执行（见 _invoke 开头 if not context.user_approved）。
#   ③ 挂 LineCountTransformer 限流，防 1e5 行命令淹没上下文。
class RunBashCommand(Tool):
    """
    用于执行 bash 命令的工具，带基于前缀的校验。

    使用 suggested_prefixes 参数对照 allow/deny 列表校验命令。
    每个命令段(以 |、&& 等分隔)都需要有自己的前缀。
    """

    # Cap returned stdout so a runaway command (e.g. `seq 1 100000`) cannot
    # flood the conversation context.
    transformers: Optional[List[Transformer]] = Field(
        default_factory=lambda: [LineCountTransformer(max_lines=200)]
    )

    name: str = "bash"
    description: str = (
        "Executes a bash command and returns its output. "
        "Supports: single commands, pipes (|), &&, ||, ;, &. "
        "Also supports (requires user approval): for/while/until loops, if/case statements, "
        "subshells $() and backticks. "
        "You must provide suggested_prefixes - one prefix per command segment. "
        "Example: for 'kubectl get pods | grep error', provide "
        "suggested_prefixes=['kubectl get', 'grep']. "
        "For scripts with loops/conditionals, provide prefixes for the key operations inside."
    )
    parameters: Dict[str, ToolParameter] = {
        "command": ToolParameter(
            description="The bash command string to execute.",
            type="string",
            required=True,
        ),
        "suggested_prefixes": ToolParameter(
            description=(
                "Array of command prefixes, one per command segment. "
                "Include command name and subcommand (e.g., 'kubectl get', 'grep'). "
                "Do NOT include resource names, namespaces, or flag values."
            ),
            type="array",
            items=ToolParameter(type="string"),
            required=True,
        ),
        "timeout": ToolParameter(
            description=(
                "Optional timeout in seconds for the command execution. "
                "Defaults to 30s."
            ),
            type="integer",
            required=False,
        ),
    }

    def _validate_command(
        self, command_str: str, suggested_prefixes: list, context: ToolInvokeContext
    ):
        """对照生效的 allow/deny 列表校验命令。"""
        # Refresh CLI-approved prefixes (no-op unless CLI mode is enabled)
        _merge_cli_approved_prefixes(context)

        config = _get_config(context)
        allow_list, deny_list = get_effective_lists(config)

        # Merge session-approved prefixes from session history (server flow)
        if context.session_approved_prefixes:
            existing = set(allow_list)
            for prefix in context.session_approved_prefixes:
                if prefix not in existing:
                    allow_list.append(prefix)

        return validate_command(command_str, suggested_prefixes, allow_list, deny_list)

    def requires_approval(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """
        基于前缀校验，判断 bash 命令是否需要审批。

        该方法在 _invoke() 之前被调用，用于确定是否需要用户审批。
        它可能被多次调用(例如，在之前的审批更新 allow 列表后重新检查)。
        """
        command_str = params.get("command", "")
        suggested_prefixes = params.get("suggested_prefixes", [])

        if not command_str or not suggested_prefixes:
            return None  # Let _invoke() handle validation errors

        validation_result = self._validate_command(
            command_str, suggested_prefixes, context
        )

        if validation_result.status == ValidationStatus.DENIED:
            # Denied commands don't need approval - they'll be rejected in _invoke()
            return None

        if validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
            prefixes_to_save = validation_result.prefixes_needing_approval or []
            return ApprovalRequirement(
                needs_approval=True,
                reason=f"Command requires approval. {validation_result.message}",
                tool_name=self.name,
                params=params,
                prefixes_to_save=prefixes_to_save,
            )

        # ALLOWED - no approval needed
        return None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        command_str = params.get("command")
        suggested_prefixes = params.get("suggested_prefixes", [])
        timeout = params.get("timeout", 30) or 30

        # Validate required parameters
        if not command_str:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'command' parameter is required and was not provided.",
                params=params,
            )

        if not isinstance(command_str, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"The 'command' parameter must be a string, got {type(command_str).__name__}.",
                params=params,
            )

        if not suggested_prefixes:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'suggested_prefixes' parameter is required. Provide one prefix per command segment.",
                params=params,
            )

        if not isinstance(suggested_prefixes, list):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"The 'suggested_prefixes' parameter must be an array, got {type(suggested_prefixes).__name__}.",
                params=params,
            )

        # If not user_approved, validate the command
        if not context.user_approved:
            validation_result = self._validate_command(
                command_str, suggested_prefixes, context
            )

            if validation_result.status == ValidationStatus.DENIED:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=self._build_deny_error_message(validation_result),
                    params=params,
                    invocation=command_str,
                )

            if validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
                # This shouldn't happen - requires_approval() should have been called first
                logging.warning(
                    f"Unexpected APPROVAL_REQUIRED in _invoke() for command: {command_str}. "
                    "This indicates requires_approval() was bypassed."
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Command requires approval but was not approved. This may be a bug.",
                    params=params,
                    invocation=command_str,
                )

        # Execute command (user_approved or validation passed)
        config = _get_config(context)
        logger.info(f"Executing bash command: {command_str}")
        try:
            result = execute_bash_command(
                cmd=command_str, timeout=int(timeout), bash_path=config.bash_path
            )
        except FileNotFoundError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Error: {e}",
                params=params,
                invocation=command_str,
            )
        return shell_result_to_structured(result, command_str, int(timeout), params)

    def _build_deny_error_message(self, validation_result) -> str:
        """根据拒绝原因构建适当的错误消息。"""
        if validation_result.deny_reason == DenyReason.HARDCODED_BLOCK:
            return f"Command blocked: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.DENY_LIST:
            return f"Command blocked by configuration: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.PREFIX_NOT_IN_COMMAND:
            return f"Invalid prefix: {validation_result.message}"

        else:
            return validation_result.message or "Command denied."


def create_bash_toolset(
    install_config: Optional[Dict[str, Any]] = None,
) -> Toolset:
    """创建 bash 工具集。

    参数:
        install_config: 可选的配置覆盖
            (例如 {"builtin_allowlist": "extended", "allow": ["docker"]})。

    返回:
        配置好的 Toolset，提供基于前缀校验的 bash 命令执行。
    """
    config = BashExecutorConfig(**(install_config or {}))

    return Toolset(
        name="bash",
        description=(
            "Execute bash commands validated against prefix-based allow/deny "
            "lists, with user approval for unknown commands."
        ),
        tools=[RunBashCommand()],
        config=config,
        type=ToolsetType.PYTHON,
        tags=[ToolsetTag.CLI],
    )
