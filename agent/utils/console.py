"""Rich-based console output helpers for the CLI.

Centralizes all user-facing formatting: banners, colors, markdown rendering,
tool result display, and status indicators.
"""

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


def print_banner(model: str, tool_count: int, compaction_enabled: bool = True) -> None:
    """Print the startup banner."""
    compaction_state = (
        Text("on", style="green") if compaction_enabled else Text("off", style="red")
    )
    banner = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
    banner.add_row(Text("🤖 Agent", style="bold magenta"), Text(f"model: {model}", style=MUTED_COLOR))
    banner.add_row(
        Text(""),
        Text(f"tools: {tool_count}  |  compaction: ", style=MUTED_COLOR) + compaction_state,
    )
    console.print(
        Panel(banner, border_style="magenta", title="[bold]Agent CLI[/bold]", title_align="left")
    )


def print_user(text: str) -> None:
    """Echo the user's question back."""
    console.print(f"[bold {USER_COLOR}]User:[/bold {USER_COLOR}] {text}")


def print_agent(text: str, elapsed: Optional[float] = None) -> None:
    """Print the agent's answer rendered as markdown inside a card."""
    console.print()
    console.print(
        _agent_panel(Markdown(text or "*(empty response)*"), elapsed=elapsed)
    )
    console.print()


def _agent_panel(body: Any, elapsed: Optional[float] = None) -> Panel:
    """Build the agent answer card, optionally showing elapsed time."""
    title = "Agent" if elapsed is None else f"Agent · {elapsed:.1f}s"
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=AGENT_COLOR,
        padding=(0, 1),
    )


class _ElapsedRenderable:
    """Spinner + text + live elapsed seconds, recomputed on each Live refresh."""

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
    """Status spinner that shows elapsed time, e.g. '⠋ Thinking … 3.2s'.

    A no-op on non-terminal outputs. Text can be changed via update() while
    the spinner keeps running; the timer keeps counting until stop().
    """

    def __init__(self, text: str = "Thinking …", start: Optional[float] = None) -> None:
        self._text = text
        self._start = start if start is not None else time.monotonic()
        self._live: Optional[Live] = None

    def start(self) -> None:
        """Start the spinner (no-op on non-terminal outputs)."""
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
        """Change the spinner label; the elapsed timer keeps running."""
        self._text = text

    def stop(self) -> None:
        """Stop the spinner; with transient=True the line disappears."""
        if self._live is not None:
            self._live.stop()
            self._live = None


def print_tool_result(
    tool_name: str,
    status: str,
    execution_time_ms: float = 0.0,
) -> None:
    """Print a single tool execution result line."""
    icon, style = _STATUS_STYLES.get(status, ("•", "white"))
    elapsed = f" ({execution_time_ms:.0f}ms)" if execution_time_ms else ""
    console.print(
        f"  [{style}]{icon} {tool_name}[/{style}]"
        f"[{MUTED_COLOR}] {status}{elapsed}[/{MUTED_COLOR}]"
    )


def print_start_tool(tool_name: str, tool_number: int = 0) -> None:
    """Print a tool call start indicator."""
    prefix = f"#{tool_number} " if tool_number else ""
    console.print(f"  [{TOOL_COLOR}]→ {prefix}{tool_name}[/{TOOL_COLOR}]", end="")


def print_approval_request(tool_name: str, params: Dict[str, Any]) -> None:
    """Print an approval request panel."""
    params_str = json.dumps(params, ensure_ascii=False, default=str)
    if len(params_str) > 400:
        params_str = params_str[:400] + "..."
    body = Text.assemble(
        ("Tool: ", "bold"), (tool_name, WARN_COLOR), "\n\n", ("Parameters: ", "bold"), params_str
    )
    console.print(Panel(body, border_style=WARN_COLOR, title="[bold]⚠ Approval required[/bold]"))


def print_compaction_start(current_tokens: Any, max_tokens: Any) -> None:
    """Print a compaction start notice."""
    console.print(
        f"  [{INFO_COLOR}]ℹ Compressing context ({current_tokens}/{max_tokens} tokens)...[/{INFO_COLOR}]"
    )


def print_compacted(old_count: int, new_count: int) -> None:
    """Print a compaction done notice."""
    console.print(
        f"  [{INFO_COLOR}]✔ Context compressed: {old_count} -> {new_count} messages[/{INFO_COLOR}]"
    )


def print_error(text: str) -> None:
    """Print an error message."""
    console.print(f"[bold {ERROR_COLOR}]Error:[/bold {ERROR_COLOR}] {text}")


def print_info(text: str) -> None:
    """Print an informational message."""
    console.print(f"[{INFO_COLOR}]ℹ {text}[/{INFO_COLOR}]")


def print_warning(text: str) -> None:
    """Print a warning message."""
    console.print(f"[{WARN_COLOR}]⚠ {text}[/{WARN_COLOR}]")


def print_rule(title: str = "") -> None:
    """Print a horizontal rule separator."""
    console.print(Rule(title=title) if title else Rule())


def print_hint(text: str) -> None:
    """Print a muted hint line (e.g. exit instructions)."""
    console.print(f"[{MUTED_COLOR}]{text}[/{MUTED_COLOR}]")


def read_piped_input() -> Optional[str]:
    """Read piped stdin data if present, else None.

    Detects whether stdin is a pipe (not a TTY) and reads it. Disabled
    under the PyCharm debugger, which reports a non-TTY stdin that
    would block on read.
    """
    if sys.stdin.isatty() or os.environ.get("PYCHARM_HOSTED"):
        return None
    try:
        data = sys.stdin.read().strip()
        return data or None
    except Exception:
        return None
