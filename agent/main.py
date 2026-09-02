"""代理（agent）的 CLI 入口。"""


# ======================= 中文导览 =======================
# 本文件是【CLI 入口】（Typer 应用），把核心 Engine 暴露成命令行。
#   命令：run(单次) / chat(交互) / serve(占位) / toolset(列出工具集) /
#         agents list / skills list|add|rm / history session|command / version。
# 关键流程：
#   Config → create_llm/create_tool_executor/create_tool_calling_llm（装配）
#   build_chat_messages → 构造 messages
#   _run_turn() → 循环跑 call_stream()，解析 StreamMessage 事件并打印；
#                 遇 APPROVAL_REQUIRED 就弹出审批交互，收集 tool_decisions 后 resume。
# 设计要点：CLI 只见 StreamMessage 事件流，不接触底层 Tool/Toolset 细节 —— 解耦清晰。
# =========================================================

import json
import logging
import sys
import time
import uuid
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
from agent.core.agents import MainAgent, Orchestrator
from agent.core.conversations import build_chat_messages
from agent.core.history.store import HistoryStore
from agent.core.skills.env_info import collect_env_info, format_env_info
from agent.core.skills.library import Skill, SkillLibrary
from agent.core.tool_calling_llm import ToolCallingLLM
from agent.core.tools import ToolsetTag
from agent.plugins.toolsets.bash.common.cli_prefixes import (
    enable_cli_mode,
    save_cli_bash_tools_approved_prefixes,
)
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

# CLI 以单一本地身份运行
CLI_TAG_FILTER = [ToolsetTag.CORE, ToolsetTag.CLI]  # 标签 ["core","cli"]

MUTED_STYLE = "bright_black"


def _log_level_for_verbosity(verbose: Optional[List[bool]]) -> Optional[str]:
    """将可重复的 -v 标志映射为日志级别。None 表示使用配置的默认值。"""
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
    """将 CLI 选项的覆盖值应用到已加载的配置上。"""
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
    """根据配置创建 LLM、工具执行器和 agent。"""
    llm = config.create_llm()
    tool_executor = config.create_tool_executor(
        toolset_tag_filter=CLI_TAG_FILTER,
    )
    agent = config.create_tool_calling_llm(
        tool_executor=tool_executor,
        llm=llm,
    )
    return llm, tool_executor, agent


def _create_multi_agent(config: Config) -> MainAgent:
    """装配多Agent 编排链路（复用组合根：create_llm / create_tool_executor）。

    主 Agent 包装现有 ToolCallingLLM（单 Agent 行为不变）；
    编排 Agent 负责拆解 + 并行调度；命令子任务由动态 SubAgent 经
    同一 ToolExecutor 执行（FR-001）。
    US3：装配本地技能库（FR-006）、环境信息快照（FR-007）与
    历史存储（FR-008），一并注入编排/主 Agent。
    """
    settings = config.multi_agent_settings()
    llm = config.create_llm()
    orchestrator_llm = llm
    if settings.get("orchestrator_model"):
        orchestrator_llm = config.create_llm()
        orchestrator_llm.model = settings["orchestrator_model"]
    tool_executor = config.create_tool_executor(
        toolset_tag_filter=CLI_TAG_FILTER,
    )
    tool_calling_llm = config.create_tool_calling_llm(
        tool_executor=tool_executor,
        llm=llm,
    )
    history = HistoryStore()
    skill_library = SkillLibrary()
    knowledge_text = format_env_info(
        collect_env_info(tool_names=list(tool_executor.tools_by_name.keys()))
    )
    orchestrator = Orchestrator(
        agent_id="orchestrator",
        llm=orchestrator_llm,
        tool_executor=tool_executor,
        max_subagents=settings.get("max_subagents", 4),
        skill_library=skill_library,
        knowledge_text=knowledge_text,
        history=history,
    )
    return MainAgent(
        agent_id="main",
        tool_calling_llm=tool_calling_llm,
        orchestrator=orchestrator,
        llm=llm,
        skill_library=skill_library,
        knowledge_text=knowledge_text,
    )


def _append_tool_result(
    session_history: List[Dict[str, Any]],
    tool_call_id: str,
    tool_name: str,
    content: str,
) -> List[Dict[str, Any]]:
    """追加一条合成的工具结果，使未完成的 assistant tool_calls
    消息得到应答，并保证消息顺序对 LLM API 仍然有效。"""
    amended = list(session_history)
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
    """执行一次 call_stream 遍历，边接收事件边打印。
    对于返回数据的处理 也是agent生成器 消费器

    返回:
        (final, pause)：final 是回合完成时的 ANSWER_END 数据；
        当流因等待用户输入而暂停时设置 pause，包含键
        kind（'approval'|'frontend'）、tool_name、tool_call_id、messages。
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
        """用给定的标签显示转圈动画，必要时重启它。"""
        nonlocal spinner
        if spinner is None:
            spinner = ElapsedSpinner(text, start=turn_start)
            spinner.start()
        else:
            spinner.update(text)

    spinner: Optional[ElapsedSpinner] = ElapsedSpinner("思考中 …", start=turn_start)
    spinner.start()

    try:
        for event in agent.call_stream( #  call_stream消费生成器
            messages=messages,
            enable_tool_approval=True,
            tool_decisions=tool_decisions,
        ):
            if event.event == StreamEvents.ANSWER_DELTA:
                # 把流式内容滚进单行转圈里（IDE 伪终端里多行 Live 刷新不可靠）；
                # 完整答案卡片在 ANSWER_END 时才一次性打印。
                streamed_text += event.data.get("content", "")
                tail = " ".join(streamed_text.split())[-40:]
                ensure_spinner(f"作答中 … {tail}")
            elif event.event == StreamEvents.ANSWER_END:
                final = event.data
                stop_spinner()
                print_agent(
                    event.data.get("content", ""),
                    elapsed=time.monotonic() - turn_start,
                )
            elif event.event == StreamEvents.START_TOOL:
                ensure_spinner(f"正在执行 {event.data.get('tool_name', '?')} …")
            elif event.event == StreamEvents.TOOL_RESULT:
                # 先停掉瞬时转圈再写结果行，rich 才能干净输出
                # （活跃的单行转圈会覆盖 IDE 伪终端里带外的 console.print）。
                stop_spinner()
                print_tool_result(
                    event.data.get("tool_name", "?"),
                    event.data.get("status", "?"),
                    event.data.get("execution_time_ms", 0.0),
                    invocation=event.data.get("invocation"),
                    return_code=event.data.get("return_code"),
                )
                ensure_spinner("思考中 …")
            elif event.event == StreamEvents.APPROVAL_REQUIRED:
                stop_spinner()
                pause = {
                    "kind": "approval",
                    "tool_name": event.data.get("tool_name", "?"),
                    "tool_call_id": event.data.get("tool_call_id", ""),
                    "reason": event.data.get("reason"),
                    "prefixes_to_save": event.data.get("prefixes_to_save") or [],
                    "pending_approvals": event.data.get("pending_approvals") or [],
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
            elif event.event == StreamEvents.MULTI_AGENT_DECOMPOSE:
                task = event.data.get("task", "")
                ensure_spinner(f"编排拆解任务 … {task[:40]}")
            elif event.event == StreamEvents.MULTI_AGENT_SUBAGENT:
                # 多Agent 事件：SubAgent 启停/结果（T017 向后兼容新增）
                stop_spinner()
                print_tool_result(
                    f"SubAgent[{event.data.get('worker', '?')}]",
                    event.data.get("state", "?"),
                    0.0,
                    invocation=event.data.get("text"),
                )
                ensure_spinner("调度中 …")
            elif event.event == StreamEvents.MULTI_AGENT_DONE:
                ensure_spinner("多Agent 任务完成 …")
            elif event.event == StreamEvents.COMPACTION_START:
                message_count = event.data.get("message_count")
                ensure_spinner("正在压缩上下文 …")
                print_compaction_start(
                    event.data.get("current_tokens", "?"),
                    event.data.get("max_tokens", "?"),
                )
            elif event.event == StreamEvents.COMPACTED:
                print_compacted(
                    message_count if message_count is not None else "...",
                    event.data.get("new_message_count", "?"),
                )
                ensure_spinner("思考中 …")
            elif event.event == StreamEvents.ERROR:
                stop_spinner()
                print_error(event.data.get("error", "未知错误"))
    finally:
        stop_spinner()

    return final, pause


def _run_turn(
    agent: ToolCallingLLM,
    messages: List[Dict[str, Any]],  # 本轮请求的初始消息
    can_prompt: bool,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """运行整个会话：循环消费流事件，途中解决审批/前端暂停。

    参数:
        agent: ToolCallingLLM 实例。
        messages: 本轮请求的初始消息。
        can_prompt: 是否可以向用户交互式询问决策。

    返回:
        (final, session_history)：final 是 ANSWER_END 数据（若会话从未以答案结束则为 None）；
        session_history 是本次回合累积的会话消息（短期记忆）。
    """
    decisions: Dict[str, bool] = {}
    # 短期记忆（session_history）：回合中不断累积的会话消息，随审批/前端暂停而更新。
    # 工作记忆只指「单次 LLM 调用实际看到的窗口」，不是整个会话。
    session_history = list(messages)

    while True:
        final, pause = _consume_stream(agent, session_history, decisions)

        if pause is None:
            session_history = final.get("messages") if final else session_history
            return final, session_history

        session_history = pause["messages"]

        if pause["kind"] == "approval":
            # 对暂停批次里每一个需要审批的调用都弹一次提示
            # （事件里每个待审批的 tool_call_id 各带一条）。
            pending: List[Dict[str, Any]] = pause.get("pending_approvals") or [
                {
                    "tool_name": pause["tool_name"],
                    "tool_call_id": pause["tool_call_id"],
                    "params": _pause_params(pause),
                    "prefixes_to_save": pause.get("prefixes_to_save") or [],
                }
            ]
            decisions = {}
            for item in pending:
                print_approval_request(
                    item.get("tool_name", "?"), item.get("params") or {}
                )
                if can_prompt:
                    approved = typer.confirm("  批准该操作？", default=False)
                else:
                    print_error(
                        "工具需要审批，但 stdin 不是交互终端，已拒绝。"
                        "如需预先放行，请把命令前缀加入 ~/.agent/config.yaml "
                        "的 `bash.allow` 列表（或设置 builtin_allowlist: extended）。"
                    )
                    approved = False

                # 引擎始终把 tool_call_id 作为 str 发出；在这里收窄类型，
                # 保证字典键在静态层面是可哈希的（否则运行时会抛 TypeError）。
                # 非 str 的一律跳过，交给孤儿工具调用兜底机制当作已取消处理。
                tool_call_id = item["tool_call_id"]
                if not isinstance(tool_call_id, str):
                    continue

                if approved:
                    decisions[tool_call_id] = True
                    # 持久化前缀会扩大所有未来会话的 allow 列表，因此必须
                    # 通过第二次提示明确征得同意（严格 opt-in）。
                    prefixes = item.get("prefixes_to_save") or []
                    if prefixes and can_prompt:
                        if typer.confirm(
                            f"  记住前缀 {prefixes}，让以后相同前缀的命令免审批？",
                            default=False,
                        ):
                            save_cli_bash_tools_approved_prefixes(prefixes)
                else:
                    # 拒绝型工具消息在恢复时由
                    # ToolCallingLLM._execute_tool_decisions 追加。
                    decisions[tool_call_id] = False

        else:  # frontend pause
            print_warning(
                f"工具 '{pause['tool_name']}' 正在等待前端执行器，"
                "但当前 CLI 没有前端，已中止该工具调用。"
            )
            session_history = _append_tool_result(
                session_history,
                pause["tool_call_id"],
                pause["tool_name"],
                "Frontend execution is not available in this environment. "
                "Tell the user this action cannot be completed.",
            )
            decisions = {}


def _pause_params(pause: Dict[str, Any]) -> Dict[str, Any]:
    """如果可用，从历史记录中提取被暂停工具调用的参数。"""
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
    """通用 LLM Agent 框架。"""
    if version_flag:
        console.print(f"agent 版本 {__version__}")
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
    # 通用选项
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
    """提出一次性问题后退出（支持管道输入 stdin）。"""
    log_file = setup_logging(_log_level_for_verbosity(verbose), install_excepthook=True)
    if log_file:
        print_hint(f"日志文件: {log_file}")
    # CLI 模式：加载 CLI 已批准的 bash 前缀，让此前的审批在这里也生效
    enable_cli_mode()
    config = Config(config_path=config_file)
    _apply_overrides(config, api_key, model, base_url, max_steps, no_compaction)

    # 提示词优先级：prompt_file > 管道 stdin > 位置参数 prompt
    piped_data = read_piped_input()
    if prompt_file and prompt:
        raise typer.BadParameter(
            "不能同时提供 prompt 参数和 --prompt-file，只能二选一。"
        )
    if prompt_file:
        if not prompt_file.is_file():
            raise typer.BadParameter(f"未找到提示词文件: {prompt_file}")
        prompt = prompt_file.read_text(encoding="utf-8")
        print_hint(f"已从文件加载提示词: {prompt_file}")

    if not prompt and not piped_data:
        raise typer.BadParameter(
            "必须提供 prompt 参数、--prompt-file，或管道 stdin 三者之一。"
        )

    # 把管道数据和 include-files 附加到提示词
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
            raise typer.BadParameter(f"未找到文件: {file_path}")
        prompt = f"{prompt}\n\nContents of {file_path.name}:\n{file_path.read_text(encoding='utf-8')}"

    if echo_request:
        print_user(prompt)
        print_rule()

    llm, tool_executor, agent = _create_agent(config)

    messages = build_chat_messages(ask=prompt, toolsets=tool_executor.enabled_toolsets)

    # 只有 stdin 是真实终端时才能交互式审批
    # （管道输入此刻已被消费完毕）。
    can_prompt = sys.stdin.isatty() and not piped_data
    final: Optional[Dict[str, Any]] = None
    try:
        final, _ = _run_turn(agent, messages, can_prompt=can_prompt)
    except KeyboardInterrupt:
        print_error("已中断。")
    except Exception as e:
        logger.debug("Agent turn failed", exc_info=True)
        print_error(f"Agent 运行失败: {e}")

    if json_output_file:
        write_json_file(
            json_output_file,
            final if final is not None else {"status": "error", "error": "No final response was produced."},
        )

    if final is None:
        raise typer.Exit(code=1)


def _chat_loop(
    agent: Any,
    toolsets: List[Any],
) -> None:
    """交互式聊天主循环：每轮消费 agent.call_stream()（单/多Agent 通用）。

    `agent` 可以是 ToolCallingLLM（单 Agent）或 MainAgent（多Agent 编排），
    二者均实现同签名 call_stream()（宪法 II：CLI 只见 StreamMessage 事件流）。
    """
    session_history: Optional[List[Dict[str, Any]]] = None

    while True:
        try:
            console.print("[bold cyan]你 >[/bold cyan]", end=" ")
            user_input = input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold magenta]再见！[/bold magenta]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            console.print("[bold magenta]再见！[/bold magenta]")
            break

        messages = build_chat_messages(
            ask=user_input,
            session_history=session_history,
            toolsets=toolsets,
        )

        try:
            # 每轮回合结束，会话的短期记忆随返回的 session_history 更新
            final, session_history = _run_turn(agent, messages, can_prompt=True)
        except KeyboardInterrupt:
            print_error("已中断。")
        except Exception as e:
            logger.debug("Agent turn failed", exc_info=True)
            print_error(f"Agent 回合失败: {e}")


@app.command()
def chat(
    # 通用选项
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    base_url: Optional[str] = opt_base_url,
    config_file: Optional[Path] = opt_config_file,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose, #
    no_compaction: bool = opt_no_compaction,
    multi_agent: bool = typer.Option(
        False,
        "--multi-agent",
        help="启用多Agent 编排模式（主/编排/业务/SubAgent 协作，contracts/cli.md）",
    ),
) -> None:
    """与 agent 开始交互式聊天会话（默认命令）。"""
    log_file = setup_logging(_log_level_for_verbosity(verbose), install_excepthook=True)
    if log_file:
        print_hint(f"日志文件: {log_file}")
    # CLI 模式：从 ~/.agent/bash_approved_prefixes.yaml 加载 CLI 已批准的 bash 前缀
    enable_cli_mode()
    config = Config(config_path=config_file)
    _apply_overrides(config, api_key, model, base_url, max_steps, no_compaction)

    if multi_agent:
        main_agent = _create_multi_agent(config)
        tool_executor = (
            main_agent.orchestrator.tool_executor
            if main_agent.orchestrator is not None
            else None
        )
        print_banner(
            model=config.data["llm"]["model"],
            tool_count=len(tool_executor.tools_by_name) if tool_executor else 0,
            compaction_enabled=(
                main_agent.tool_calling_llm.enable_compaction
                if main_agent.tool_calling_llm
                else False
            ),
        )
        print_hint(
            "多Agent 编排模式：主/编排/业务/SubAgent 协作。"
            "输入你的问题，'/exit' 退出。\n"
        )
        # FR-008：会话生命周期落历史（open → close）。
        history = main_agent.orchestrator.history if main_agent.orchestrator else None
        session_id = f"cli-{uuid.uuid4().hex[:8]}"
        if history is not None:
            history.append_session(session_id=session_id, payload={"status": "open"})
        try:
            _chat_loop(
                main_agent,
                tool_executor.enabled_toolsets if tool_executor else [],
            )
        finally:
            if history is not None:
                history.append_session(
                    session_id=session_id, payload={"status": "closed"}
                )
        return

    llm, tool_executor, agent = _create_agent(config) # 创建llm ,tool

    print_banner(
        model=llm.model,
        tool_count=len(tool_executor.tools_by_name),
        compaction_enabled=agent.enable_compaction,
    )
    print_hint("输入你的问题，'/exit' 或 Ctrl+C 退出。\n")

    _chat_loop(agent, tool_executor.enabled_toolsets)


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", help="Server host"),
    port: Optional[int] = typer.Option(None, "--port", help="Server port"),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """以 FastAPI 服务器形式启动 agent（占位）。

    实现后，服务器将使用 toolset_tag_filter=[CORE, CLUSTER]
    从服务器 API 中排除仅 CLI 专用的工具集。
    """
    print_hint("服务器模式尚未实现，敬请期待。")
    print_hint(f"将监听 {host or '0.0.0.0'}:{port or 8000}")
    print_hint("标签过滤: [CORE, CLUSTER]（服务器模式）")


@app.command()
def toolset(
    config_file: Optional[Path] = opt_config_file,
    verbose: Optional[List[bool]] = opt_verbose,
) -> None:
    """列出可用的工具集及其状态。"""
    setup_logging(_log_level_for_verbosity(verbose))
    config = Config(config_path=config_file)
    # 这里不需要 LLM —— 只列出工具注册表。
    tool_executor = config.create_tool_executor(
        toolset_tag_filter=CLI_TAG_FILTER,
    )

    table = Table(title="工具集", show_lines=False)
    table.add_column("工具集", style="bold")
    table.add_column("状态")
    table.add_column("类型", style=MUTED_STYLE)
    table.add_column("标签", style=MUTED_STYLE)
    table.add_column("工具数", justify="right")

    for ts in tool_executor.toolsets:
        loaded = ts in tool_executor.enabled_toolsets
        status = "[green]已启用[/green]" if loaded else "[bright_black]已过滤[/bright_black]"
        table.add_row(
            ts.name,
            status,
            ts.type.value if ts.type else "-",
            ", ".join(t.value for t in ts.tags) or "-",
            str(len(ts.tools)),
        )

    console.print(table)
    console.print(
        f"[bright_black]已加载 {len(tool_executor.tools_by_name)} 个工具 "
        f"（标签过滤: CORE, CLI）[/bright_black]"
    )


agents_app = typer.Typer(
    name="agents",
    help="多Agent 编排相关命令（角色查看等）",
    no_args_is_help=True,
)


@agents_app.command("list")
def agents_list(
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """列出多Agent 框架的固定角色与动态 SubAgent 上限（contracts/cli.md）。"""
    config = Config(config_path=config_file)
    settings = config.multi_agent_settings()
    table = Table(title="多Agent 角色")
    table.add_column("角色", style="bold")
    table.add_column("职责")
    table.add_column("类型", style=MUTED_STYLE)
    rows = [
        ("main", "用户交互与整体调度（现有 agent 包装）", "固定"),
        ("orchestrator", "任务拆解与并行调度", "固定"),
        ("business", "业务/领域子任务处理", "固定"),
        ("subagent", "命令子任务执行", f"动态（上限 {settings.get('max_subagents', 4)}）"),
    ]
    for role, duty, kind in rows:
        table.add_row(role, duty, kind)
    console.print(table)
    if not settings.get("enabled", False):
        print_hint("multi_agent.enabled=false，可用 `agent chat --multi-agent` 临时启用。")


app.add_typer(agents_app)


# ======================= US3：技能库管理（FR-006） =======================
skills_app = typer.Typer(
    name="skills",
    help="本地技能库管理（US3 FR-006，contracts/cli.md）",
    no_args_is_help=True,
)


def _print_skills(lib: SkillLibrary) -> None:
    table = Table(title="本地技能库")
    table.add_column("名称", style="bold")
    table.add_column("描述")
    table.add_column("绑定工具", style=MUTED_STYLE)
    for skill in lib.list():
        table.add_row(
            skill.name,
            skill.description,
            ", ".join(skill.tool_bindings) or "-",
        )
    console.print(table)
    if not lib.list():
        print_hint("技能库为空，可用 `agent skills add <skill.yaml>` 添加。")


@skills_app.command("list")
def skills_list() -> None:
    """列出本地技能库中的技能。"""
    _print_skills(SkillLibrary())


@skills_app.command("add")
def skills_add(
    skill_file: Path = typer.Argument(..., help="技能 YAML 文件路径"),
) -> None:
    """从 YAML 文件添加/更新一条技能（data-model.md Skill）。"""
    if not skill_file.exists():
        raise typer.BadParameter(f"技能文件不存在: {skill_file}")
    import yaml

    data = yaml.safe_load(skill_file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not data.get("name"):
        print_error(f"技能文件缺少 name 字段: {skill_file}")
        raise typer.Exit(code=1)
    lib = SkillLibrary()
    path = lib.add(
        Skill(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            instructions=str(data.get("instructions", "")),
            tool_bindings=[str(t) for t in (data.get("tool_bindings") or [])],
            keywords=[str(k) for k in (data.get("keywords") or [])],
        )
    )
    console.print(f"[green]已添加技能[/green] {data['name']} → {path}")


@skills_app.command("rm")
def skills_rm(
    name: str = typer.Argument(..., help="技能名"),
) -> None:
    """从本地技能库删除一条技能。"""
    if SkillLibrary().remove(name):
        console.print(f"[green]已删除技能[/green]: {name}")
    else:
        print_error(f"技能不存在: {name}")
        raise typer.Exit(code=1)


app.add_typer(skills_app)


# ======================= US3：历史/日志查询（FR-008） =======================
history_app = typer.Typer(
    name="history",
    help="查询执行日志/会话历史（US3 FR-008，contracts/cli.md）",
    no_args_is_help=True,
)


def _print_history_records(records: List[Any], title: str) -> None:
    table = Table(title=title)
    table.add_column("类型", style="bold")
    table.add_column("时间", style=MUTED_STYLE)
    table.add_column("会话", style=MUTED_STYLE)
    table.add_column("Agent", style=MUTED_STYLE)
    table.add_column("内容")
    for rec in records:
        payload = rec.payload
        summary = (
            str(payload.get("command", ""))
            if "command" in payload
            else json.dumps(payload, ensure_ascii=False, default=str)
        )
        table.add_row(
            rec.type,
            rec.created_at,
            rec.session_id or "-",
            rec.agent_id or "-",
            summary[:120],
        )
    console.print(table)
    if not records:
        print_hint("没有匹配的历史记录。")


@history_app.command("session")
def history_session(
    session_id: str = typer.Argument(..., help="会话 ID"),
) -> None:
    """查询指定会话的历史记录。"""
    _print_history_records(
        HistoryStore().query_session(session_id), title=f"会话历史: {session_id}"
    )


@history_app.command("command")
def history_command(
    pattern: str = typer.Argument(..., help="命令文本匹配模式（子串，不区分大小写）"),
) -> None:
    """按命令文本模式查询执行记录。"""
    _print_history_records(
        HistoryStore().query_command(pattern), title=f"命令历史: {pattern}"
    )


app.add_typer(history_app)


@app.command()
def version() -> None:
    """显示 agent 版本。"""
    console.print(f"agent 版本 {__version__}")


def main() -> None:
    """console_scripts 的入口点。未指定子命令时默认为 'chat'。"""
    if len(sys.argv) == 1:
        sys.argv.insert(1, "chat")
    app()


if __name__ == "__main__":
    main()
