"""面向 CLI 的基于 Rich 的控制台输出辅助函数。

集中所有面向用户的格式化：横幅、颜色、markdown 渲染、
工具结果展示和状态指示器。
"""

# ======================= 中文导览 =======================
# CLI 的【门面/输出层】：把内部状态渲染给人类看的唯一出口（用 rich）。
# 职责：banner / 用户与 agent 发言卡片 / 工具执行状态行 / 审批面板 / 压缩提示 / 错误信息
#       / 转圈(ElapsedSpinner，显示已耗秒数，非终端自动 no-op) / 读管道输入。
# 设计理念：
#   ① 主循环不直接 print，而是产出结构化事件，由这里映射成「图标 + 颜色 + 排版」，
#      使展示与业务核心解耦、可换终端。
#   _STATUS_STYLES 把状态码映射成 symbol+颜色（✔ green / ✖ red / ∅ yellow / ⚠ …）。
#   ② 启动时强制 stdout/stderr 为 UTF-8——Windows 老代码页(GBK)编不了 ✔⚠ℹ。
# 消费方：agent/main.py 的 CLI 交互层。
# =========================================================

import json
import os
import sys
import time
from typing import Any, Callable, Dict, Optional

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

# Windows consoles often default to a legacy codepage (e.g. GBK) that cannot
# encode symbols like ✔ ⚠ ℹ. Force UTF-8 on stdout/stderr so rich output works.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# Shared console instance
console = Console()

# Color scheme
USER_COLOR = "cyan"
AGENT_COLOR = "green"
TOOL_COLOR = "yellow"
ERROR_COLOR = "red"
WARN_COLOR = "bold yellow"
INFO_COLOR = "blue"
MUTED_COLOR = "bright_black"

# Tool status display
_STATUS_STYLES = {
    "success": ("✔", "green"),
    "error": ("✖", "red"),
    "no_data": ("∅", "yellow"),
    "approval_required": ("⚠", "bold yellow"),
    "frontend_pause": ("⏸", "blue"),
}

# 状态码 → 中文标签（展示用，不影响内部 enum 值）。
_STATUS_LABELS = {
    "success": "成功",
    "error": "错误",
    "no_data": "无数据",
    "approval_required": "需审批",
    "frontend_pause": "等待前端",
}


def print_banner(model: str, tool_count: int, compaction_enabled: bool = True) -> None:
    """打印启动横幅。"""
    compaction_state = (
        Text("开", style="green") if compaction_enabled else Text("关", style="red")
    )
    banner = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
    banner.add_row(Text("🤖 Agent", style="bold magenta"), Text(f"模型: {model}", style=MUTED_COLOR))
    banner.add_row(
        Text(""),
        Text(f"工具数: {tool_count}  |  上下文压缩: ", style=MUTED_COLOR) + compaction_state,
    )
    console.print(
        Panel(banner, border_style="magenta", title="[bold]Agent 命令行[/bold]", title_align="left")
    )


def print_user(text: str) -> None:
    """回显用户的问题。"""
    console.print(f"[bold {USER_COLOR}]用户:[/bold {USER_COLOR}] {text}")


def print_agent(text: str, elapsed: Optional[float] = None) -> None:
    """打印 agent 的回答，在卡片内以 markdown 渲染。"""
    console.print()
    console.print(
        _agent_panel(Markdown(text or "*(空回复)*"), elapsed=elapsed)
    )
    console.print()


def _agent_panel(body: Any, elapsed: Optional[float] = None) -> Panel:
    """构建 agent 回答卡片，可选地显示已耗时间。"""
    title = "Agent" if elapsed is None else f"Agent · {elapsed:.1f}s"
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=AGENT_COLOR,
        padding=(0, 1),
    )


class _ElapsedRenderable:
    """转圈动画 + 文本 + 实时已耗秒数，每次 Live 刷新时重新计算。"""

    def __init__(self, get_text: Callable[[], str], start: float) -> None:
        self._get_text = get_text
        self._start = start

    def __rich_console__(self, console: Console, options: Any) -> Any:
        elapsed = time.monotonic() - self._start
        yield Spinner(
            "dots",
            Text(f" {self._get_text()} {elapsed:.1f}s", style=MUTED_COLOR),
        )


class ElapsedSpinner:
    """显示已耗时间的状态转圈动画，例如 '⠋ Thinking … 3.2s'。

    在非终端输出上为空操作。转圈运行期间可通过 update() 更改文本；
    计时器会持续计时，直到调用 stop()。
    """
    # 行为对象（机器）：主循环耗时（思考/工具执行）的 live 转圈显示。
    # start()/update()/stop() 三态；内部 _ElapsedRenderable 每次刷新重新算已耗秒数。
    # 非终端(管道/日志)时 start() 直接跳过，避免阻塞与乱码。

    def __init__(self, text: str = "思考中 …", start: Optional[float] = None) -> None:
        self._text = text
        self._start = start if start is not None else time.monotonic()
        self._live: Optional[Live] = None

    def start(self) -> None:
        """启动转圈动画（在非终端输出上为空操作）。"""
        if not console.is_terminal:
            return
        self._live = Live(
            _ElapsedRenderable(lambda: self._text, self._start),
            console=console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.start()

    def update(self, text: str) -> None:
        """更改转圈标签；已耗计时器保持运行。"""
        self._text = text

    def stop(self) -> None:
        """停止转圈动画；transient=True 时该行会消失。"""
        if self._live is not None:
            self._live.stop()
            self._live = None


def print_tool_result(
    tool_name: str,
    status: str,
    execution_time_ms: float = 0.0,
    invocation: Optional[str] = None,
    return_code: Optional[int] = None,
) -> None:
    """打印单条工具执行结果行。

    当提供了 invocation/return_code（例如用户批准的 bash 命令）时，
    会显示它们，以便执行的命令与退出码清晰可见。
    """
    icon, style = _STATUS_STYLES.get(status, ("•", "white"))
    elapsed = f" (耗时 {execution_time_ms:.0f}ms)" if execution_time_ms else ""
    rc = f" 退出码={return_code}" if return_code is not None else ""
    cmd = f"  [{MUTED_COLOR}]{invocation}[/{MUTED_COLOR}]" if invocation else ""
    status_label = _STATUS_LABELS.get(status, status)
    console.print(
        f"  [{style}]{icon} {tool_name}[/{style}]"
        f"[{MUTED_COLOR}]{rc} {status_label}{elapsed}[/{MUTED_COLOR}]{cmd}"
    )


def print_start_tool(tool_name: str, tool_number: int = 0) -> None:
    """打印工具调用开始指示符。"""
    prefix = f"#{tool_number} " if tool_number else ""
    console.print(f"  [{TOOL_COLOR}]→ {prefix}{tool_name}[/{TOOL_COLOR}]", end="")


def print_approval_request(tool_name: str, params: Dict[str, Any]) -> None:
    """打印审批请求面板。"""
    params_str = json.dumps(params, ensure_ascii=False, default=str)
    if len(params_str) > 400:
        params_str = params_str[:400] + "..."
    body = Text.assemble(
        ("工具: ", "bold"), (tool_name, WARN_COLOR), "\n\n", ("参数: ", "bold"), params_str
    )
    console.print(Panel(body, border_style=WARN_COLOR, title="[bold]⚠ 需要审批[/bold]"))


def print_compaction_start(current_tokens: Any, max_tokens: Any) -> None:
    """打印压缩开始提示。"""
    console.print(
        f"  [{INFO_COLOR}]ℹ 正在压缩上下文（{current_tokens}/{max_tokens} tokens）...[/{INFO_COLOR}]"
    )


def print_compacted(old_count: int, new_count: int) -> None:
    """打印压缩完成提示。"""
    console.print(
        f"  [{INFO_COLOR}]✔ 上下文已压缩: {old_count} -> {new_count} 条消息[/{INFO_COLOR}]"
    )


def print_error(text: str) -> None:
    """打印错误消息。"""
    console.print(f"[bold {ERROR_COLOR}]错误:[/bold {ERROR_COLOR}] {text}")


def print_info(text: str) -> None:
    """打印信息性消息。"""
    console.print(f"[{INFO_COLOR}]ℹ {text}[/{INFO_COLOR}]")


def print_warning(text: str) -> None:
    """打印警告消息。"""
    console.print(f"[{WARN_COLOR}]⚠ {text}[/{WARN_COLOR}]")


def print_rule(title: str = "") -> None:
    """打印水平分隔线。"""
    console.print(Rule(title=title) if title else Rule())


def print_hint(text: str) -> None:
    """打印低调的提示行（例如退出说明）。"""
    console.print(f"[{MUTED_COLOR}]{text}[/{MUTED_COLOR}]")


def read_piped_input() -> Optional[str]:
    """如果存在管道输入则读取 stdin 数据，否则返回 None。

    检测 stdin 是否为管道（而非 TTY）并读取。在 PyCharm 调试器下禁用，
    因为它报告的 stdin 是非 TTY 的，读取时会阻塞。
    """
    if sys.stdin.isatty() or os.environ.get("PYCHARM_HOSTED"):
        return None
    try:
        data = sys.stdin.read().strip()
        return data or None
    except Exception:
        return None
