"""CLI entry point for the agent."""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.table import Table

from agent import __version__
from agent.common.cli_commons import (
    opt_api_key,
    opt_base_url,
    opt_config_file,
    opt_json_output_file,
    opt_max_steps,
    opt_model,
    opt_no_compaction,
    opt_verbose,
)
from agent.config import Config
from agent.core.conversations import build_chat_messages
from agent.core.tool_calling_llm import ToolCallingLLM
from agent.core.tools import ToolsetTag
from agent.utils.console import (
    ElapsedSpinner,
    console,
    print_agent,
    print_approval_request,
    print_banner,
    print_compacted,
    print_compaction_start,
    print_error,
    print_hint,
    print_rule,
    print_tool_result,
    print_user,
    print_warning,
    read_piped_input,
)
from agent.utils.file_utils import write_json_file
from agent.utils.log import setup_logging
from agent.utils.stream import StreamEvents

app = typer.Typer(
    name="agent",
    help="General-purpose LLM Agent framework",
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
    no_args_is_help=False,
)

logger = logging.getLogger(__name__)

# CLI runs as a single local identity
CLI_TAG_FILTER = [ToolsetTag.CORE, ToolsetTag.CLI]

MUTED_STYLE = "bright_black"


def _log_level_for_verbosity(verbose: Optional[List[bool]]) -> Optional[str]:
    """Map repeatable -v flags to a log level. None = use configured default."""
    count = len(verbose or [])
    if count >= 2:
        return "DEBUG"
    if count == 1:
        return "INFO"
    return None


def _apply_overrides(
    config: Config,
    api_key: Optional[str],
    model: Optional[str],
    base_url: Optional[str],
    max_steps: Optional[int],
    no_compaction: bool,
) -> None:
    """Apply CLI option overrides onto the loaded config."""
    if api_key:
        config.data["llm"]["api_key"] = api_key
    if model:
        config.data["llm"]["model"] = model
    if base_url:
        config.data["llm"]["base_url"] = base_url
    if max_steps:
        config.data["agent"]["max_steps"] = max_steps
    if no_compaction:
        config.data["agent"]["enable_compaction"] = False


def _create_agent(config: Config):
    """Create the LLM, tool executor and agent from config."""
    llm = config.create_llm()
    tool_executor = config.create_tool_executor(
        toolset_tag_filter=CLI_TAG_FILTER,
    )
    agent = config.create_tool_calling_llm(
        tool_executor=tool_executor,
        llm=llm,
    )
    return llm, tool_executor, agent


def _append_tool_result(
    history: List[Dict[str, Any]],
    tool_call_id: str,
    tool_name: str,
    content: str,
) -> List[Dict[str, Any]]:
    """Append a synthetic tool result so a dangling assistant tool_calls
    message is answered and the message ordering stays valid for the LLM API."""
    amended = list(history)
    amended.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": content,
        }
    )
    return amended


def _consume_stream(
    agent: ToolCallingLLM,
    messages: List[Dict[str, Any]],
    tool_decisions: Dict[str, bool],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Run one call_stream pass, printing events as they arrive.

    Returns:
        (final, pause): final is the ANSWER_END data when the turn completed;
        pause is set when the stream paused for user input, with keys
        kind ('approval'|'frontend'), tool_name, tool_call_id, messages.
    """
    final: Optional[Dict[str, Any]] = None
    pause: Optional[Dict[str, Any]] = None
    message_count: Optional[int] = None
    streamed_text = ""

    turn_start = time.monotonic()

    def stop_spinner() -> None:
        nonlocal spinner
        if spinner is not None:
            spinner.stop()
            spinner = None

    def ensure_spinner(text: str) -> None:
        """Show the spinner with the given label, restarting it if needed."""
        nonlocal spinner
        if spinner is None:
            spinner = ElapsedSpinner(text, start=turn_start)
            spinner.start()
        else:
            spinner.update(text)

    spinner: Optional[ElapsedSpinner] = ElapsedSpinner("Thinking …", start=turn_start)
    spinner.start()

    for event in agent.call_stream(
        messages=messages,
        enable_tool_approval=True,
        tool_decisions=tool_decisions,
    ):
        if event.event == StreamEvents.ANSWER_DELTA:
            # Stream into the single-line spinner (multi-line Live refresh is
            # unreliable in IDE pseudo-terminals); the full answer card is
            # printed once at ANSWER_END.
            streamed_text += event.data.get("content", "")
            tail = " ".join(streamed_text.split())[-40:]
            ensure_spinner(f"Answering … {tail}")
        elif event.event == StreamEvents.ANSWER_END:
            final = event.data
            stop_spinner()
            print_agent(
                event.data.get("content", ""),
                elapsed=time.monotonic() - turn_start,
            )
        elif event.event == StreamEvents.START_TOOL:
            ensure_spinner(f"Running {event.data.get('tool_name', '?')} …")
        elif event.event == StreamEvents.TOOL_RESULT:
            print_tool_result(
                event.data.get("tool_name", "?"),
                event.data.get("status", "?"),
                event.data.get("execution_time_ms", 0.0),
            )
            ensure_spinner("Thinking …")
        elif event.event == StreamEvents.APPROVAL_REQUIRED:
            stop_spinner()
            pause = {
                "kind": "approval",
                "tool_name": event.data.get("tool_name", "?"),
                "tool_call_id": event.data.get("tool_call_id", ""),
                "messages": event.data.get("messages") or list(messages),
            }
            return final, pause
        elif event.event == StreamEvents.FRONTEND_PAUSE:
            stop_spinner()
            pause = {
                "kind": "frontend",
                "tool_name": event.data.get("tool_name", "?"),
                "tool_call_id": event.data.get("tool_call_id", ""),
                "messages": event.data.get("messages") or list(messages),
            }
            return final, pause
        elif event.event == StreamEvents.COMPACTION_START:
            message_count = event.data.get("message_count")
            ensure_spinner("Compressing context …")
            print_compaction_start(
                event.data.get("current_tokens", "?"),
                event.data.get("max_tokens", "?"),
            )
        elif event.event == StreamEvents.COMPACTED:
            print_compacted(
                message_count if message_count is not None else "...",
                event.data.get("new_message_count", "?"),
            )
            ensure_spinner("Thinking …")
        elif event.event == StreamEvents.ERROR:
            stop_spinner()
            print_error(event.data.get("error", "Unknown error"))

    stop_spinner()

    return final, pause


def _run_turn(
    agent: ToolCallingLLM,
    messages: List[Dict[str, Any]],
    can_prompt: bool,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run one full agent turn, resolving approval/frontend pauses along the way.

    Args:
        agent: ToolCallingLLM instance.
        messages: Initial messages for this turn.
        can_prompt: Whether the user can be asked interactively for decisions.

    Returns:
        (final, history): final is the ANSWER_END data (None if the turn never
        completed with an answer); history is the conversation so far.
    """
    decisions: Dict[str, bool] = {}
    working = list(messages)

    while True:
        final, pause = _consume_stream(agent, working, decisions)
        if pause is None:
            history = final.get("messages") if final else working
            return final, history

        working = pause["messages"]

        if pause["kind"] == "approval":
            print_approval_request(pause["tool_name"], _pause_params(pause))
            if can_prompt:
                approved = typer.confirm("  Approve?", default=False)
            else:
                print_error(
                    "Tool requires approval but stdin is not interactive; denying."
                )
                approved = False
            if approved:
                decisions = {pause["tool_name"]: True}
            else:
                working = _append_tool_result(
                    working,
                    pause["tool_call_id"],
                    pause["tool_name"],
                    "User denied approval for this tool call.",
                )
                decisions = {}
        else:  # frontend pause
            print_warning(
                f"Tool '{pause['tool_name']}' paused waiting for a frontend "
                "executor, which is unavailable in this CLI. Aborting the tool call."
            )
            working = _append_tool_result(
                working,
                pause["tool_call_id"],
                pause["tool_name"],
                "Frontend execution is not available in this environment. "
                "Tell the user this action cannot be completed.",
            )
            decisions = {}


def _pause_params(pause: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the paused tool call's parameters from its history, if available."""
    for msg in reversed(pause["messages"]):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("id") == pause["tool_call_id"]:
                try:
                    return json.loads(tc.get("function", {}).get("arguments", "{}"))
                except Exception:
                    return {}
    return {}


@app.callback()
def main_callback(
    version_flag: bool = typer.Option(
        False, "--version", help="Show the agent version and exit"
    ),
) -> None:
    """General-purpose LLM Agent framework."""
    if version_flag:
        console.print(f"agent version {__version__}")
        raise typer.Exit()


@app.command()
def run(
    prompt: Optional[str] = typer.Argument(None, help="What to ask the LLM (user prompt)"),
    prompt_file: Optional[Path] = typer.Option(
        None,
        "--prompt-file",
        "-pf",
        help="File containing the prompt to ask the LLM (overrides the prompt argument)",
    ),
    include_file: Optional[List[Path]] = typer.Option(
        [],
        "--file",
        "-f",
        help="File to append to the prompt (can specify -f multiple times)",
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    base_url: Optional[str] = opt_base_url,
    config_file: Optional[Path] = opt_config_file,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    no_compaction: bool = opt_no_compaction,
    json_output_file: Optional[str] = opt_json_output_file,
    echo_request: bool = typer.Option(
        True,
        "--echo/--no-echo",
        help="Echo back the question provided to the agent in the output",
    ),
) -> None:
    """Ask a one-shot question and exit (supports piped stdin)."""
    setup_logging(_log_level_for_verbosity(verbose))
    config = Config(config_path=config_file)
    _apply_overrides(config, api_key, model, base_url, max_steps, no_compaction)

    # Prompt priority: prompt_file > piped stdin > positional prompt
    piped_data = read_piped_input()
    if prompt_file and prompt:
        raise typer.BadParameter(
            "You cannot provide both a prompt argument and a prompt file. Use one or the other."
        )
    if prompt_file:
        if not prompt_file.is_file():
            raise typer.BadParameter(f"Prompt file not found: {prompt_file}")
        prompt = prompt_file.read_text(encoding="utf-8")
        print_hint(f"Loaded prompt from file {prompt_file}")

    if not prompt and not piped_data:
        raise typer.BadParameter(
            "Either the 'prompt' argument, '--prompt-file', or piped stdin must be provided."
        )

    # Attach piped data and include-files to the prompt
    if piped_data:
        if prompt:
            prompt = f"Here's some piped output:\n\n{piped_data}\n\n{prompt}"
        else:
            prompt = (
                f"Here's some piped output:\n\n{piped_data}\n\n"
                "What can you tell me about this output?"
            )

    for file_path in include_file or []:
        if not file_path.is_file():
            raise typer.BadParameter(f"File not found: {file_path}")
        prompt = f"{prompt}\n\nContents of {file_path.name}:\n{file_path.read_text(encoding='utf-8')}"

    if echo_request:
        print_user(prompt)
        print_rule()

    llm, tool_executor, agent = _create_agent(config)

    messages = build_chat_messages(ask=prompt, toolsets=tool_executor.enabled_toolsets)

    # Interactive approval is only possible when stdin is a real terminal
    # (piped input has already been consumed at this point).
    can_prompt = sys.stdin.isatty() and not piped_data
    final: Optional[Dict[str, Any]] = None
    try:
        final, _ = _run_turn(agent, messages, can_prompt=can_prompt)
    except Exception as e:
        logger.debug("Agent turn failed", exc_info=True)
        print_error(f"Agent run failed: {e}")

    if json_output_file:
        write_json_file(
            json_output_file,
            final if final is not None else {"status": "error", "error": "No final response was produced."},
        )

    if final is None:
        raise typer.Exit(code=1)


@app.command()
def chat(
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    base_url: Optional[str] = opt_base_url,
    config_file: Optional[Path] = opt_config_file,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    no_compaction: bool = opt_no_compaction,
) -> None:
    """Start an interactive chat session with the agent (default command)."""
    setup_logging(_log_level_for_verbosity(verbose))
    config = Config(config_path=config_file)
    _apply_overrides(config, api_key, model, base_url, max_steps, no_compaction)

    llm, tool_executor, agent = _create_agent(config)

    print_banner(
        model=llm.model,
        tool_count=len(tool_executor.tools_by_name),
        compaction_enabled=agent.enable_compaction,
    )
    print_hint("Type your question. '/exit' or Ctrl+C to quit.\n")

    conversation_history: Optional[List[Dict[str, Any]]] = None

    while True:
        try:
            console.print("[bold cyan]You >[/bold cyan]", end=" ")
            user_input = input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold magenta]Goodbye![/bold magenta]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            console.print("[bold magenta]Goodbye![/bold magenta]")
            break

        messages = build_chat_messages(
            ask=user_input,
            conversation_history=conversation_history,
            toolsets=tool_executor.enabled_toolsets,
        )

        try:
            final, history = _run_turn(agent, messages, can_prompt=True)
            if history:
                conversation_history = history
        except KeyboardInterrupt:
            print_error("Interrupted.")
        except Exception as e:
            logger.debug("Agent turn failed", exc_info=True)
            print_error(f"Agent turn failed: {e}")


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", help="Server host"),
    port: Optional[int] = typer.Option(None, "--port", help="Server port"),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """Start the agent as a FastAPI server (placeholder).

    When implemented, the server will use toolset_tag_filter=[CORE, CLUSTER]
    to exclude CLI-specific toolsets from the server API.
    """
    print_hint("Server mode is not yet implemented. Coming soon.")
    print_hint(f"Would listen on {host or '0.0.0.0'}:{port or 8000}")
    print_hint("Tag filter: [CORE, CLUSTER] (server mode)")


@app.command()
def toolset(
    config_file: Optional[Path] = opt_config_file,
    verbose: Optional[List[bool]] = opt_verbose,
) -> None:
    """List available toolsets and their status."""
    setup_logging(_log_level_for_verbosity(verbose))
    config = Config(config_path=config_file)
    _, tool_executor, _ = _create_agent(config)

    table = Table(title="Toolsets", show_lines=False)
    table.add_column("Toolset", style="bold")
    table.add_column("Status")
    table.add_column("Type", style=MUTED_STYLE)
    table.add_column("Tags", style=MUTED_STYLE)
    table.add_column("Tools", justify="right")

    for ts in tool_executor.toolsets:
        loaded = ts in tool_executor.enabled_toolsets
        status = "[green]enabled[/green]" if loaded else "[bright_black]filtered[/bright_black]"
        table.add_row(
            ts.name,
            status,
            ts.type.value if ts.type else "-",
            ", ".join(t.value for t in ts.tags) or "-",
            str(len(ts.tools)),
        )

    console.print(table)
    console.print(
        f"[bright_black]{len(tool_executor.tools_by_name)} tools loaded "
        f"(tag filter: CORE, CLI)[/bright_black]"
    )


@app.command()
def version() -> None:
    """Show the agent version."""
    console.print(f"agent version {__version__}")


def main() -> None:
    """Entry point for console_scripts. Defaults to 'chat' when no subcommand is given."""
    if len(sys.argv) == 1:
        sys.argv.insert(1, "chat")
    app()


if __name__ == "__main__":
    main()