"""轻量沙箱工具集：子进程隔离 + 超时 + 受控工作目录 + 资源上限（FR-003/004）。

安全分层（research.md §4「在现有 bash 工具集校验层之上」）：
  ① 复用 bash 校验层（validation.py 前缀白/黑名单 + argv 安全规则）做审批判定
     —— 高风险/未知命令执行前要求审批（FR-003），敏感路径绝对拒绝；
  ② 经 terminal.execute_in_shell 跨终端执行（探测 + 同义改写 + 超时整树杀灭）；
  ③ 受控工作目录（cwd）+ 超时秒数 + 输出行数上限（LineCountTransformer）作为资源上限。

沙箱不可用（类型非 lightweight、shell 二进制缺失）→ 返回明确错误，绝不静默失败
（FR-004）。容器化沙箱（Docker）为 v2 增强，见 research.md §4。
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from agent.core.env.terminal import TerminalType, execute_in_shell
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
from agent.plugins.toolsets.bash.validation import (
    ValidationStatus,
    get_effective_lists,
    validate_command,
)
from agent.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


# ---- 行为对象的配置（盒子）：沙箱规则 ----
# 继承 ToolsetConfig（extra="allow"，宪法 III 向后兼容）。字段与 BashExecutorConfig
# 对齐（builtin_allowlist/allow/deny 复用 bash 校验层的生效名单语义）。
class SandboxExecutorConfig(ToolsetConfig):
    """轻量沙箱工具集配置。"""

    # v1 仅支持 lightweight（子进程隔离）；container 为 v2 占位 → 明确报错（FR-004）。
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


def _get_config(context: ToolInvokeContext) -> SandboxExecutorConfig:
    """从调用上下文读取沙箱工具集配置。"""
    toolset = getattr(context, "toolset", None)
    if toolset is not None and toolset.config is not None:
        return toolset.config
    return SandboxExecutorConfig()


class RunSandboxCommand(Tool):
    """轻量沙箱命令执行工具。

    与 bash 工具同构（command + suggested_prefixes 走审批层），但：
    - 经 execute_in_shell 跨终端执行（探测 + 同义改写 + 超时）；
    - 支持受控工作目录（cwd）与超时（timeout）参数。
    """

    # 防一条命令的输出淹没上下文（资源上限之一）。
    transformers: Optional[List[Transformer]] = Field(
        default_factory=lambda: [LineCountTransformer(max_lines=200)]
    )

    name: str = "sandbox"
    description: str = (
        "Executes a command in a controlled lightweight sandbox (subprocess "
        "isolation, timeout, working directory). Supports bash/zsh/powershell "
        "syntax; commands are validated against prefix-based allow/deny lists, "
        "and unknown/high-risk commands require approval. "
        "You must provide suggested_prefixes - one prefix per command segment. "
        "Example: for 'kubectl get pods | grep error', provide "
        "suggested_prefixes=['kubectl get', 'grep']."
    )
    parameters: Dict[str, ToolParameter] = {
        "command": ToolParameter(
            description="The command string to execute.",
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
        "shell": ToolParameter(
            description=(
                "Optional target shell: 'bash', 'zsh' or 'powershell'. "
                "Defaults to auto-detection."
            ),
            type="string",
            required=False,
        ),
        "timeout": ToolParameter(
            description=(
                "Optional timeout in seconds for the command execution. "
                "Defaults to the toolset timeout_seconds (30s)."
            ),
            type="integer",
            required=False,
        ),
        "cwd": ToolParameter(
            description=(
                "Optional controlled working directory for the command. "
                "Defaults to the toolset working_dir / current directory."
            ),
            type="string",
            required=False,
        ),
    }

    def _validate(
        self, command_str: str, suggested_prefixes: list, config: SandboxExecutorConfig
    ):
        allow_list, deny_list = get_effective_lists(config)
        return validate_command(command_str, suggested_prefixes, allow_list, deny_list)

    def requires_approval(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """基于前缀校验判断命令是否需要审批（复用 bash 审批层，FR-003）。

        DENIED（黑名单/敏感路径）不在此返回审批 —— _invoke 会直接拒绝；
        APPROVAL_REQUIRED（未命中白名单）→ 返回审批请求，暂停等用户决断。
        """
        command_str = params.get("command", "")
        suggested_prefixes = params.get("suggested_prefixes", [])
        if not command_str or not suggested_prefixes:
            return None
        config = _get_config(context)
        validation = self._validate(command_str, suggested_prefixes, config)
        if validation.status == ValidationStatus.DENIED:
            return None  # 会在 _invoke 中直接拒绝（不可被审批豁免）
        if validation.status == ValidationStatus.APPROVAL_REQUIRED:
            return ApprovalRequirement(
                needs_approval=True,
                reason=f"Command requires approval. {validation.message}",
                tool_name=self.name,
                params=params,
                prefixes_to_save=validation.prefixes_needing_approval or [],
            )
        return None  # ALLOWED 免批直跑

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        command_str = params.get("command", "")
        suggested_prefixes = params.get("suggested_prefixes", [])
        config = _get_config(context)

        # FR-004：容器化沙箱 v1 未实现 → 明确错误，不静默执行。
        if config.type != "lightweight":
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Sandbox type '{config.type}' is not implemented in v1; "
                    "only 'lightweight' is supported."
                ),
                params=params,
                invocation=command_str,
            )

        if not command_str or not suggested_prefixes:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="'command' and 'suggested_prefixes' parameters are required.",
                params=params,
            )

        # 未获人工批准 → 走校验层：DENIED 直接拒绝，APPROVAL_REQUIRED 报错
        # （正常路径下 requires_approval() 已在 invoke() 步骤①拦截审批请求）。
        if not context.user_approved:
            validation = self._validate(command_str, suggested_prefixes, config)
            if validation.status == ValidationStatus.DENIED:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=validation.message or "Command denied.",
                    params=params,
                    invocation=command_str,
                )
            if validation.status == ValidationStatus.APPROVAL_REQUIRED:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Command requires approval but was not approved.",
                    params=params,
                    invocation=command_str,
                )

        # 执行：跨终端探测 + 同义改写 + 超时 + 受控工作目录。
        timeout = int(params.get("timeout") or config.timeout_seconds)
        cwd = params.get("cwd") or config.working_dir or ""
        shell_value = params.get("shell") or ""
        shell = TerminalType(shell_value) if shell_value else None

        try:
            result = execute_in_shell(
                command_str, shell=shell, timeout=timeout, cwd=cwd, bash_path=config.bash_path
            )
        except FileNotFoundError as exc:
            # FR-004：shell 二进制缺失 → 明确错误提示，不静默。
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Sandbox execution failed (shell unavailable): {exc}",
                params=params,
                invocation=command_str,
            )

        # 统一结果转换（execute_in_shell 直接返回 ShellResult，经共享转换器格式化）。
        return shell_result_to_structured(result, command_str, timeout, params)


def create_sandbox_toolset(
    install_config: Optional[Dict[str, Any]] = None,
) -> Toolset:
    """创建轻量沙箱工具集。

    参数:
        install_config: 可选的配置覆盖
            (例如 {"builtin_allowlist": "extended", "timeout_seconds": 60})。

    返回:
        配置好的 Toolset，提供受控沙箱命令执行。
    """
    config = SandboxExecutorConfig(**(install_config or {}))

    return Toolset(
        name="sandbox",
        description=(
            "Execute commands in a controlled lightweight sandbox (subprocess "
            "isolation, timeout, working directory), validated against "
            "prefix-based allow/deny lists with approval for unknown commands."
        ),
        tools=[RunSandboxCommand()],
        config=config,
        type=ToolsetType.PYTHON,
        tags=[ToolsetTag.CLI],
    )


__all__ = ["RunSandboxCommand", "SandboxExecutorConfig", "create_sandbox_toolset"]
