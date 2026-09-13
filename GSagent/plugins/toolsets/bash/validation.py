"""
bash 工具集的基于前缀的命令校验。

本模块基于前缀匹配提供 bash 命令校验逻辑：对照 allow/deny 列表进行匹配，
并支持组合命令(管道、&& 等)。移植自 holmesgpt；bashlex 解析与平台无关，
在 Windows(Git Bash)和 POSIX 上行为一致。
"""

# ======================= 中文导览 =======================
# 校验层：对 bash 命令做安全判定，产出 ValidationResult(ALLOWED/DENIED/APPROVAL_REQUIRED)。
#   入口 validate_command(command, suggested_prefixes, allow, deny)——被 RunBashCommand 调用。
# 判定管线（validate_segment，每个命令段依次走）：
#   ① 硬编码块(sudo/su) → DENIED(不可豁免)
#   ② 敏感路径(凭据/配置) → DENIED(不可豁免)
#   ③ 黑名单 → DENIED
#   ④ 白名单前缀 → ALLOWED
#   ⑤ 二者皆未命中 → APPROVAL_REQUIRED
# 另有 check_dangerous_argv()：前缀只认命令【名】，却拦不住白名单命令带危险【参数】
#   把只读工具变成写/执行（find -exec、重定向写真实文件、sort --compress-program 等）——
#   基于 bashlex 解析的 AST 判定，命中即 DENIED/需审批，绝不自动执行。
# =========================================================

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional, Tuple

import bashlex
from bashlex import ast

from GSagent.plugins.toolsets.bash.argv_utils import is_benign_redirect_target
from GSagent.plugins.toolsets.bash.command_arg_rules import (
    dangerous_argv_reason,
    is_argv_checked_command,
)
from GSagent.plugins.toolsets.bash.common.config import (
    HARDCODED_BLOCKS,
    BashExecutorConfig,
)
from GSagent.plugins.toolsets.bash.common.default_lists import (
    CORE_ALLOW_LIST,
    DEFAULT_DENY_LIST,
    EXTENDED_ALLOW_LIST,
    SENSITIVE_PATH_PATTERNS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument-level (argv) security checks.
#
# Prefix matching validates only a command's *name*. Some allow-listed commands
# accept arguments (or shell redirections) that turn a read-only tool into
# arbitrary code execution, file writes, or deletion. These checks inspect the
# parsed argv/redirections and DENY those primitives regardless of allow-list
# membership or prior approval, so they are never auto-executed. (This operates
# on the parsed AST; commands bashlex cannot parse are routed to approval by
# validate_command and so are never auto-executed either.)
#
# Scope note: `tar`/`zcat`/`zgrep`/`gzip` are intentionally NOT in the builtin
# allow lists (see default_lists.py); any use of them already requires approval,
# so they need no argv rule here.
#
# The per-command rules (find/sort/uniq) live in command_arg_rules.py and the
# generic argv/target helpers in argv_utils.py; this module turns a reported
# reason into a DENY/APPROVAL verdict.
# ---------------------------------------------------------------------------


# bashlex word sub-part kinds that expand at shell runtime into text the static
# argv check cannot see: `$(...)`/backticks, `$VAR`/`${VAR}`, and `<(...)`.
# Detecting these from the AST (not the dequoted string) means a *quoted* literal
# like '*$(x)*' - which the shell never expands - is correctly treated as inert.
DYNAMIC_WORD_PART_KINDS = frozenset(
    {"parameter", "commandsubstitution", "processsubstitution"}
)


def _word_node_has_dynamic_expansion(word_node: Any) -> bool:
    """如果 bashlex WordNode 包含运行时展开的子部分，则返回 True。"""
    return any(
        getattr(part, "kind", None) in DYNAMIC_WORD_PART_KINDS
        for part in (getattr(word_node, "parts", None) or [])
    )


class ValidationStatus(Enum):
    """命令校验的结果状态。"""

    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"


class DenyReason(Enum):
    """命令被拒绝的原因。"""

    HARDCODED_BLOCK = "hardcoded_block"
    DENY_LIST = "deny_list"
    PREFIX_NOT_IN_COMMAND = "fabricated_prefix"
    DANGEROUS_ARGUMENT = "dangerous_argument"
    SENSITIVE_PATH = "sensitive_path"


# ---- 值对象：校验结果 ----
# 输入：校验函数产出；输出：喂回 RunBashCommand 决定免批/拒绝/要审批。
@dataclass
class ValidationResult:
    """命令校验的结果。"""

    status: ValidationStatus
    deny_reason: Optional[DenyReason] = None
    message: Optional[str] = None
    # Prefixes that need approval (for APPROVAL_REQUIRED status)
    prefixes_needing_approval: Optional[List[str]] = None


# 得到生效的白/黑名单：builtin_allowlist 选内置档位(none/core/extended)，再并上用户 allow/deny。
# 返回【副本】，避免调用方误改共享 config。
def get_effective_lists(config: BashExecutorConfig) -> Tuple[List[str], List[str]]:
    """
    根据配置获取生效的 allow 和 deny 列表。

    builtin_allowlist 控制将哪个内置列表与用户提供的条目合并：
    - "core"：文本/JSON 处理、系统信息、只读的 git/kubectl
    - "extended"：core + 文件系统命令(cat、find、ls、base64)
    - "none"：仅使用用户提供的 allow/deny 条目

    返回副本，以避免修改共享的 config。

    返回:
        (allow_list, deny_list) 元组 —— 始终返回副本，绝不返回引用
    """
    if config.builtin_allowlist == "extended":
        builtin = EXTENDED_ALLOW_LIST
    elif config.builtin_allowlist == "core":
        builtin = CORE_ALLOW_LIST
    else:
        builtin = []

    allow_list = sorted(set(builtin + config.allow))
    deny_list = sorted(set(DEFAULT_DENY_LIST + config.deny))

    return allow_list, deny_list


class CommandSegmentExtractor(ast.nodevisitor):
    """
    提取命令段的 Bashlex AST 访问器(visitor)。

    遇到复合语句时设置 contains_compound_command 标志，
    但仍会继续遍历以提取内部的命令段。

    此外，还为 argv 层安全校验收集以下信息：
    - command_argvs：每个简单命令节点的单词列表(argv)
    - write_redirect_targets：输出重定向到真实文件的目标
    """

    def __init__(self, command: str):
        self.command = command
        self.segments: List[str] = []
        self.contains_compound_command: bool = False
        self.command_argvs: List[List[str]] = []
        # Parallel to command_argvs: True if any *argument* (not argv[0]) contains
        # a runtime expansion, so the parsed argv may differ from what the shell runs.
        self.command_arg_dynamic: List[bool] = []
        self.write_redirect_targets: List[str] = []

    def visitcommand(self, node, *args, **kwargs):
        """提取简单命令的命令文本。"""
        cmd_text = self.command[node.pos[0] : node.pos[1]].strip()
        self.segments.append(cmd_text)
        word_parts = [part for part in node.parts if getattr(part, "kind", None) == "word"]
        if word_parts:
            self.command_argvs.append([part.word for part in word_parts])
            self.command_arg_dynamic.append(
                any(_word_node_has_dynamic_expansion(part) for part in word_parts[1:])
            )

    def visitcompound(self, node, *args, **kwargs):
        """标记复合语句，但继续遍历以提取内部的命令段。"""
        self.contains_compound_command = True

    def visitredirect(self, node, input, type, output, heredoc):  # noqa: A002
        """记录目标为真实文件(而非文件描述符)的输出重定向。

        当重定向的 type 包含 '>' 且其 output 是单词(路径)而非整数(fd，例如 `2>&1`)时，
        该重定向会写入文件。写入 /dev/null、/dev/stdout 和 /dev/stderr 是安全的，会被忽略。
        """
        if type and ">" in type and hasattr(output, "word"):
            target = output.word
            if not is_benign_redirect_target(target):
                self.write_redirect_targets.append(target)


def _build_extractor(command: str) -> CommandSegmentExtractor:
    """解析命令，并在其上运行段/argv/重定向提取器。

    异常:
        bashlex.errors.ParsingError: 如果 bashlex 无法解析该命令
        NotImplementedError: 如果 bashlex 遇到不支持的语法(例如 case 语句)
    """
    extractor = CommandSegmentExtractor(command)
    for part in bashlex.parse(command):
        extractor.visit(part)
    return extractor


def parse_command_segments(command: str) -> Tuple[List[str], bool]:
    """
    将命令按 |、&&、||、;、& 解析为多个段(segment)。

    使用 bashlex AST 访问器进行正确的 shell 解析。

    返回:
        (segments, contains_compound_command) 元组：
        - segments: 从命令中提取的命令段列表
        - contains_compound_command: 是否检测到复合语句(for、while、if 等)

    异常:
        bashlex.errors.ParsingError: 如果 bashlex 无法解析该命令
        NotImplementedError: 如果 bashlex 遇到不支持的语法(例如 case 语句)
    """
    extractor = _build_extractor(command)
    return (extractor.segments, extractor.contains_compound_command)


def _unsafe_arg_result(reason: str, approval_mode: bool) -> ValidationResult:
    """阻止执行/写入向量：默认返回 DENIED，或(在审批模式下)返回 APPROVAL_REQUIRED
    以便人工允许。无论哪种方式，都绝不会自动执行。"""
    if approval_mode:
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message=(
                f"Command requires approval: {reason}. The bash toolset is "
                "read-only, so this is not auto-executed."
            ),
            prefixes_needing_approval=[],
        )
    return ValidationResult(
        status=ValidationStatus.DENIED,
        deny_reason=DenyReason.DANGEROUS_ARGUMENT,
        message=(
            f"Command blocked for security reasons: {reason}. The bash toolset "
            "is read-only; this is not auto-executed."
        ),
    )


def check_dangerous_argv(extractor: CommandSegmentExtractor) -> Optional[ValidationResult]:
    """前缀匹配看不到的 argv 层与重定向安全校验。

    返回:
        - 如果任一命令段使用了危险参数原语，或通过输出重定向写入真实文件，
          则返回 DENIED(最先检查，因此硬性拒绝绝不会被降级为审批)；
        - 如果某个被 argv 检查的命令(find/sort/uniq)通过 shell 展开构建参数，
          其运行时值无法静态检查、可能夹带被拦截的原语，则返回 APPROVAL_REQUIRED；
        - 否则返回 None。

    该检查作用于解析后的 AST，因此适用于 bashlex 能解析的命令。
    bashlex 无法解析的命令永远不会走到这里 —— validate_command 会将它们路由到
    APPROVAL_REQUIRED(人工介入)，因此它们绝不会被自动执行。

    执行/写入向量的处理方式由 AGENT_BASH_UNSAFE_ARGS_MODE 决定：
      - "deny"(默认)：直接拦截(它们绝不会被自动执行，也无法被审批)；
      - "approval"：仍不会被自动执行，但人工可以批准真正只读的用法(例如 `find … -exec grep …`)。
    任何其他取值都回退到 "deny"。无论哪种方式都不会自动运行任何内容 ——
    它只是在拦截和提示人工之间做出选择。
    """
    # Unknown/empty values fail safe to the strict "deny" behaviour.
    approval_mode = (
        os.environ.get("AGENT_BASH_UNSAFE_ARGS_MODE", "deny").strip().lower()
        == "approval"
    )

    # DENY (or, in approval mode, gate) checks first, across ALL segments, so a
    # hard block is never downgraded by an earlier segment that merely contains a
    # shell expansion.
    for argv in extractor.command_argvs:
        reason = dangerous_argv_reason(argv)
        if reason:
            return _unsafe_arg_result(reason, approval_mode)

    if extractor.write_redirect_targets:
        target = extractor.write_redirect_targets[0]
        return _unsafe_arg_result(
            f"output redirection to '{target}' writes to the filesystem",
            approval_mode,
        )

    # No hard deny. A runtime expansion ($(...), `...`, $VAR/${VAR}, <(...)) in an
    # argv-checked command's arguments can expand into a blocked primitive that the
    # static checks above cannot see, so require explicit approval rather than
    # auto-allowing it.
    for argv, arg_is_dynamic in zip(
        extractor.command_argvs, extractor.command_arg_dynamic, strict=True
    ):
        if arg_is_dynamic and is_argv_checked_command(os.path.basename(argv[0])):
            return ValidationResult(
                status=ValidationStatus.APPROVAL_REQUIRED,
                message=(
                    f"'{os.path.basename(argv[0])}' builds an argument via shell "
                    "expansion, which cannot be verified as read-only and requires "
                    "approval."
                ),
                prefixes_needing_approval=[],
            )

    return None


def check_hardcoded_blocks(segment: str) -> Optional[str]:
    """
    检查段是否匹配任何硬编码拦截模式。
    为保持一致，使用与 deny 列表相同的匹配逻辑。

    参数:
        segment: 单个命令段(已解析)

    返回:
        若找到匹配的拦截模式则返回该模式，否则返回 None
    """
    segment_lower = segment.lower()
    for block in HARDCODED_BLOCKS:
        if match_prefix_for_deny(segment_lower, block):
            return block

    return None


def check_blocked_in_raw_command(command: str, blocked_list: List[str]) -> Optional[str]:
    """
    使用单词边界在原始命令字符串中任意位置检查被拦截的模式。

    这是当 bashlex 无法解析命令时的兜底安全检查。
    它会扫描整个原始命令，查找给定列表中的任何模式。

    参数:
        command: 完整的原始命令字符串(可能包含复合语句、子 shell 等)
        blocked_list: 要检查的命令模式列表(例如 HARDCODED_BLOCKS 或 deny_list)

    返回:
        若找到匹配的模式则返回该模式，否则返回 None
    """
    command_lower = command.lower()
    for pattern in blocked_list:
        if re.search(rf"\b{re.escape(pattern.lower())}\b", command_lower):
            return pattern
    return None


def match_prefix(segment: str, prefix: str) -> bool:
    """
    检查命令段是否匹配某个前缀。

    前缀应在单词边界处匹配命令的开头。
    允许将空格或 '/' 作为有效边界(适用于 kubectl resource/name 语法)。

    示例:
        - "kubectl get pods" 匹配前缀 "kubectl get"
        - "kubectl delete pod" 不匹配前缀 "kubectl get"
        - "grep -r error" 匹配前缀 "grep"
        - "kubectl get secret/my-secret" 匹配前缀 "kubectl get secret"
    """
    segment = segment.strip()
    prefix = prefix.strip()

    if not segment.startswith(prefix):
        return False

    # If prefix is shorter than segment, the next char must be boundary char or end
    if len(segment) > len(prefix):
        next_char = segment[len(prefix)]
        # Allow whitespace or path separator as boundary
        if not (next_char.isspace() or next_char == "/"):
            return False

    return True


def match_prefix_for_deny(segment: str, prefix: str) -> bool:
    """
    检查命令段是否匹配 deny 列表前缀。

    比 allow 列表匹配更严格，以防止安全绕过：
    - 将 '/' 视为有效边界(可捕获 'kubectl get secret/name' 语法)
    - 同时匹配复数形式(前缀 + 's')以捕获资源类型别名

    示例:
        - "kubectl get secret/my-secret" 匹配前缀 "kubectl get secret"
        - "kubectl get secrets" 匹配前缀 "kubectl get secret"(复数)
        - "kubectl get secrets/my-secret" 匹配前缀 "kubectl get secret"
    """
    segment = segment.strip()
    prefix = prefix.strip()

    # Exact prefix match at a valid boundary (reuses match_prefix, which
    # already accepts whitespace or '/' as boundary chars)
    if match_prefix(segment, prefix):
        return True

    # Plural form (handles 'secret' matching 'secrets')
    if match_prefix(segment, prefix + "s"):
        return True

    return False


def check_sensitive_paths(text: str) -> Optional[str]:
    """若 *text* 中匹配到敏感路径模式，则返回第一个匹配的模式。

    对整段文本进行不区分大小写的匹配，因此白名单前缀(例如 `cat`)绝不能被
    用来读取凭据(~/.ssh/id_rsa、~/.env 等)。拒绝是绝对的：审批也无法覆盖。
    """
    text_lower = text.lower()
    for pattern in SENSITIVE_PATH_PATTERNS:
        if re.search(pattern, text_lower):
            return pattern
    return None


def _sensitive_path_result(pattern: str) -> ValidationResult:
    return ValidationResult(
        status=ValidationStatus.DENIED,
        deny_reason=DenyReason.SENSITIVE_PATH,
        message=(
            f"Command accesses a sensitive path (matched '{pattern}'). "
            "Reading credentials or agent configuration is not allowed."
        ),
    )


def validate_segment(
    segment: str, allow_list: List[str], deny_list: List[str]
) -> ValidationResult:
    """
    对照 allow/deny 列表校验单个命令段。

    校验顺序:
    1. 硬编码拦截块 -> DENIED
    2. 敏感路径 -> DENIED
    3. deny 列表 -> DENIED
    4. allow 列表 -> ALLOWED
    5. 两者皆未命中 -> APPROVAL_REQUIRED
    """
    # Step 1: Check hardcoded blocks
    blocked = check_hardcoded_blocks(segment)
    if blocked:
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.HARDCODED_BLOCK,
            message=(
                f"Command contains '{blocked}' which is permanently blocked "
                "for security reasons and cannot be overridden."
            ),
        )

    # Step 2: Check sensitive paths (credentials/config files) - cannot be
    # overridden by allow lists or approval.
    sensitive = check_sensitive_paths(segment)
    if sensitive:
        return _sensitive_path_result(sensitive)

    # Step 3: Check deny list (using stricter matching)
    for deny_prefix in deny_list:
        if match_prefix_for_deny(segment, deny_prefix):
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.DENY_LIST,
                message=f"Command matches deny list pattern '{deny_prefix}'. This command is blocked by configuration.",
            )

    # Step 4: Check allow list
    for allow_prefix in allow_list:
        if match_prefix(segment, allow_prefix):
            return ValidationResult(status=ValidationStatus.ALLOWED)

    # Step 5: Not in any list -> needs approval
    return ValidationResult(
        status=ValidationStatus.APPROVAL_REQUIRED,
        message=f"Command segment '{segment}' is not in the allow list.",
    )


def validate_command(
    command: str,
    suggested_prefixes: List[str],
    allow_list: List[str],
    deny_list: List[str],
) -> ValidationResult:
    """
    对照 allow/deny 列表校验 bash 命令。

    参数:
        command: 要校验的完整 bash 命令
        suggested_prefixes: AI 提供的前缀(每个命令段一个)
        allow_list: 允许的命令前缀列表
        deny_list: 被拒绝的命令前缀列表

    返回:
        包含状态和详情信息的 ValidationResult
    """
    # Verify all suggested prefixes actually appear in the command.
    # Word-boundary match: a bare substring check would let the prefix
    # "echo" satisfy the command "myecho hi".
    for prefix in suggested_prefixes:
        if not re.search(rf"(?<![\w.-]){re.escape(prefix)}(?![\w.-])", command):
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.PREFIX_NOT_IN_COMMAND,
                message=f"Suggested prefix '{prefix}' does not appear in the command.",
            )

    # Parse command into segments and detect compound statements
    try:
        extractor = _build_extractor(command)
    except (bashlex.errors.ParsingError, NotImplementedError):
        # Can't parse - do safety checks on raw string, then ask user to approve
        blocked = check_blocked_in_raw_command(command, HARDCODED_BLOCKS)
        if blocked:
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.HARDCODED_BLOCK,
                message=(
                f"Command contains '{blocked}' which is permanently blocked "
                "for security reasons and cannot be overridden."
            ),
            )
        sensitive = check_sensitive_paths(command)
        if sensitive:
            return _sensitive_path_result(sensitive)
        denied = check_blocked_in_raw_command(command, deny_list)
        if denied:
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.DENY_LIST,
                message=f"Command matches deny list pattern '{denied}'. This command is blocked by configuration.",
            )
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Command contains complex syntax which requires approval.",
            prefixes_needing_approval=[],
        )

    segments = extractor.segments
    contains_compound_command = extractor.contains_compound_command

    # Validate each segment against deny/allow lists
    unapproved_segments: List[str] = []

    for segment in segments:
        result = validate_segment(segment, allow_list, deny_list)

        # If any segment is denied, the whole command is denied
        if result.status == ValidationStatus.DENIED:
            return result

        if result.status == ValidationStatus.APPROVAL_REQUIRED:
            unapproved_segments.append(segment)

    # Argv-level security check: code-exec/write primitives and output
    # redirections that prefix matching cannot see (applies to every command
    # node, including those inside pipes/compound statements). Runs AFTER the
    # per-segment loop so a hardcoded-block / deny-list DENY there is never
    # pre-empted by an argv approval (e.g. the shell-expansion gate, or an
    # exec/write vector in approval mode).
    dangerous = check_dangerous_argv(extractor)
    if dangerous:
        return dangerous

    # Compound commands always require approval, even if all segments are allowed.
    # Only unapproved-segment approvals save prefixes to the allow list -
    # compound and unparseable approvals are one-time only.
    if contains_compound_command:
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Contains compound statements (for/while/if/etc).",
            prefixes_needing_approval=[],
        )

    if unapproved_segments:
        prefixes_needing_approval = list(
            dict.fromkeys(
                prefix
                for prefix in suggested_prefixes
                if not any(match_prefix(prefix, allowed) for allowed in allow_list)
            )
        )
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message=f"Segment(s) not in allow list: {', '.join(repr(s) for s in unapproved_segments)}",
            prefixes_needing_approval=prefixes_needing_approval,
        )

    # All segments validated and allowed
    return ValidationResult(status=ValidationStatus.ALLOWED)
