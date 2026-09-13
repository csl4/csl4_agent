"""Bash 工具集逐命令参数（argv）危险规则。

prefix 白名单只校验命令的*名称*。少数白名单命令接受的参数可以把只读工具变成任意代码
执行、写文件或删除文件。本文件为每个这样的命令配置一个检查器：它接收命令的参数
（去掉 argv[0] 的 argv），若命中危险则返回人可读的原因，否则返回 None。`validation.py`
把返回的原因转成 DENY/APPROVAL 判定；通用的 argv 解析在 argv_utils.py 中。

要覆盖新命令：
  1. 在下方添加一个 `_<cmd>_reason(args)` 检查器，并且
  2. 在 `_ARGV_CHECKERS` 中注册它。
（同时为白名单保护测试补一个用例。）
"""

import os
from typing import List, Optional

from GSagent.plugins.toolsets.bash.argv_utils import (
    abbreviates,
    is_benign_redirect_target,
    parse_argv,
)

# ======================= 中文导览 =======================
# Bash 的【逐命令参数危险规则】：prefix 白名单只校验命令【名】。
#   少数白名单命令接收的参数能把「只读工具」变成任意代码执行/写文件/删文件。
#   每一个这样的命令在此配一个检查器 `_<cmd>_reason(args)`：
#     输入：该命令的参数（去掉 argv[0] 的剩余命令行）。
#     输出：命中危险原语 → 一句人话原因；否则 None（安全）。
#   validation.py 拿到返回的原因转成 DENY / APPROVAL 判定；通用 argv 解析在 argv_utils.py。
# 当前覆盖：
#   find  → 动作原语 -exec/-delete/-fprint…（执行/写删）。
#   sort  → --compress-program 执行程序；-o/--output 写文件。
#   uniq  → 第二个位置参数是输出文件（除非是 '-'/标准流等无害目标）。
# 如何新增覆盖：① 加一个 _<cmd>_reason()；② 在 _ARGV_CHECKERS 注册；再补测试。
# 设计理念：按命令【basename】分派（如截 kubectl get 不误伤 sort -o），
#           宁严勿松——用 abbreviates 把 `--out` 视为 `--output`。
# =========================================================

# --- find ---------------------------------------------------------------------
# `find` action primitives that execute commands or write/delete files. `find`
# uses word-style primaries (no getopt clustering), so exact-token matching is
# correct here.
FIND_DANGEROUS_PRIMITIVES = frozenset(
    {
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",  # execute a command
        "-delete",  # delete matched files
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",  # write to an arbitrary file
    }
)


def _find_reason(args: List[str]) -> Optional[str]:
    """`find` 的动作原语（-exec/-delete/-fprint…）会执行命令或写入文件。"""
    for arg in args:
        if arg in FIND_DANGEROUS_PRIMITIVES:
            return f"'find' argument '{arg}' can execute commands or write/delete files"
    return None


# --- sort ---------------------------------------------------------------------
# Value-taking options, so short-option clusters parse correctly (the `o` in
# `sort -to` is `-t`'s value, not `-o`).
SORT_VALUE_SHORT_CHARS = frozenset("ktSTo")
SORT_VALUE_LONG_OPTS = frozenset(
    {
        "--output",
        "--compress-program",
        "--buffer-size",
        "--key",
        "--field-separator",
        "--temporary-directory",
        "--batch-size",
        "--files0-from",
        "--random-source",
    }
)
# Options that write a file, or execute a program on spill. Long options are
# matched by prefix-abbreviation (GNU getopt_long accepts `--out` for `--output`);
# `-o` is the short output option.
SORT_WRITE_LONG_OPTS = frozenset({"--output"})
SORT_EXEC_LONG_OPTS = frozenset({"--compress-program"})


def _sort_reason(args: List[str]) -> Optional[str]:
    """`sort --compress-program` 会执行程序；`sort -o`/`--output` 会写入文件。"""
    options, _ = parse_argv(args, SORT_VALUE_SHORT_CHARS, SORT_VALUE_LONG_OPTS)
    long_opts = [opt for opt in options if opt.startswith("--")]
    if any(abbreviates(opt, SORT_EXEC_LONG_OPTS) for opt in long_opts):
        return "'sort --compress-program' can execute an arbitrary program"
    if "-o" in options or any(abbreviates(opt, SORT_WRITE_LONG_OPTS) for opt in long_opts):
        return "'sort' output-file option writes to the filesystem"
    return None


# --- uniq ---------------------------------------------------------------------
UNIQ_VALUE_SHORT_CHARS = frozenset("fsw")
UNIQ_VALUE_LONG_OPTS = frozenset({"--skip-fields", "--skip-chars", "--check-chars"})


def _uniq_positional_args(args: List[str]) -> List[str]:
    """返回一次 `uniq` 调用中的位置参数（非选项参数）。"""
    _, positionals = parse_argv(args, UNIQ_VALUE_SHORT_CHARS, UNIQ_VALUE_LONG_OPTS)
    return positionals


def _uniq_reason(args: List[str]) -> Optional[str]:
    """`uniq [OPTION]... [INPUT [OUTPUT]]` - 第 2 个位置参数是输出文件，
    除非它是标准流 / '-'（stdout），即并非真实文件。"""
    positionals = _uniq_positional_args(args)
    if len(positionals) >= 2 and not (
        positionals[1] == "-" or is_benign_redirect_target(positionals[1])
    ):
        return "'uniq' output-file argument writes to the filesystem"
    return None


# --- dispatch -----------------------------------------------------------------
# Command basename -> checker. Private: callers use the functions below rather
# than reaching into the registry.
_ARGV_CHECKERS = {
    "find": _find_reason,
    "sort": _sort_reason,
    "uniq": _uniq_reason,
}


def dangerous_argv_reason(argv: List[str]) -> Optional[str]:
    """若 argv 使用了代码执行/写入/删除原语则返回人可读的原因，否则返回 None。
    分派范围限定在命令的 basename 上，因此例如 `sort -o` 会被拦截，
    而 `kubectl get -o wide` 不会被误伤。"""
    if not argv:
        return None
    checker = _ARGV_CHECKERS.get(os.path.basename(argv[0]))
    return checker(argv[1:]) if checker else None


def is_argv_checked_command(name: str) -> bool:
    """若 `name`（命令 basename）在此有逐参数规则则返回 True。"""
    return name in _ARGV_CHECKERS
