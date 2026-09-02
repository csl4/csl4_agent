"""Bash 工具集的通用 argv / 重定向目标辅助函数。

纯函数、与具体命令无关的工具，被 command_arg_rules.py 中的逐命令规则和
validation.py 中的重定向检测共用。此处不依赖任何具体命令。
"""

from typing import List, Tuple

# ======================= 中文导览 =======================
# Bash 的【通用 argv / 重定向目标工具】——纯函数、与具体命令无关，
#   被 command_arg_rules.py 的逐命令检查器和 validation.py 的重定向检测共用。
# 由 prefix 白名单「只管命令名」的短板出发：要把白名单命令的「读」防成「写/执行」，
#   就得认得参数。这里提供三件事：
#   is_benign_redirect_target() → 判断重定向目标是不是「非真实文件」（如 >/dev/null），
#                                 写它无害，不该当危险行为拦。
#   abbreviates(opt, targets)   → GNU getopt_long 接受唯一前缀缩写，所以 `--out` 要当
#                                 `--output`；安全上宁可多匹配（过拟合=安全）。
#   parse_argv(...)             → 精简版 getopt：只够区分「选项 vs 位置参数」、
#                                 处理短选项集群(-ro == -r -o)和吃后续token的选项(-f 2)，
#                                 不是完整 getopt。
# 设计理念：这里宁「严」勿「松」——解析不准就多报险，绝不漏拦真危险参数。
# =========================================================

# Redirection / output targets that are not real files (writing to them is
# benign): the null sink, the standard streams, the terminal, and fd aliases.
BENIGN_REDIRECT_TARGETS = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
)


def is_benign_redirect_target(target: str) -> bool:
    """判断重定向/输出目标是否不是磁盘上的真实文件。"""
    return target in BENIGN_REDIRECT_TARGETS or target.startswith("/dev/fd/")


def abbreviates(opt: str, targets: frozenset) -> bool:
    """若 `opt`（例如 '--out'）是 `targets` 中任一长选项的非空前缀缩写则返回 True。
    GNU getopt_long 接受无歧义的缩写，因此安全检查必须把 `--out` 当作 `--output`。
    宁可多匹配（安全）：真实工具会拒绝的歧义缩写仍会被标记出来。"""
    return len(opt) > 2 and opt.startswith("--") and any(t.startswith(opt) for t in targets)


def parse_argv(
    args: List[str],
    value_short_chars: frozenset,
    value_long_opts: frozenset,
) -> Tuple[set, List[str]]:
    """对命令参数做精简版 getopt 风格解析。

    它模拟仅校验名称会漏掉的两种情况：短选项集群（`-ro` == `-r -o`）以及
    把下一个 token 作为其值的选项（`-f 2`、`--skip-fields 2`）。它只精确到
    足以区分选项与位置参数、并知道出现了哪些选项字母的程度——并非完整的
    getopt 实现。

    参数:
        value_short_chars: 短选项需要取值的单个字母。
        value_long_opts: 需要把单独 token 作为取值的 `--name` 长选项。

    返回:
        (options_present, positionals)，其中 options_present 保存类似
        '-o' / '--output' 的 token，positionals 保存非选项参数。
    """
    options: set = set()
    positionals: List[str] = []
    i = 0
    end_of_options = False
    while i < len(args):
        arg = args[i]
        if end_of_options or arg == "-" or not arg.startswith("-"):
            positionals.append(arg)  # '-' (stdin) counts as a positional
            i += 1
            continue
        if arg == "--":
            end_of_options = True
            i += 1
            continue
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            options.add(name)
            # A required-value long option consumes the next token unless the
            # value was given inline as --name=value. Match by abbreviation.
            i += 2 if ("=" not in arg and abbreviates(name, value_long_opts)) else 1
            continue
        # Short-option cluster, e.g. -c, -cf, -ro, -ofile.
        consumes_next = False
        for pos in range(1, len(arg)):
            options.add("-" + arg[pos])
            if arg[pos] in value_short_chars:
                # The value is the rest of this token if present, else the next
                # token. Either way the cluster ends here.
                consumes_next = pos == len(arg) - 1
                break
        i += 2 if consumes_next else 1
    return options, positionals
