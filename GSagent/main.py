"""代理（agent）的 CLI 入口。"""


# ======================= 中文导览 =======================
# 本文件是【CLI 入口】（Typer 应用），把核心 Engine 暴露成命令行。
#   命令：run(单次) / chat(交互) / serve(占位) / toolset(列出工具集) /
#         agents list / skills list|add|rm / history session|command / version。
# 关键流程：
#   Config → create_single_graph_agent / create_multi_graph_agent /
#            create_plan_graph_agent（纯 langgraph 装配，SqliteSaver 持久化）
#   build_chat_messages → 构造 messages
#   _run_turn() → 循环消费 run_graph_session：langgraph custom 渲染事件 +
#                 __interrupt__ 审批暂停（per-interrupt-id resume）。
# 设计要点：CLI 直接消费 langgraph 原生流（custom/updates），不接触底层
# Tool/图内部细节（拆壳后无 GraphAgent/StreamMessage/PauseRequest 对象层）。
# =========================================================

import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.table import Table

from GSagent import __version__
from GSagent.common import DEFAULT_SNAPSHOT_DIR
from GSagent.common.cli_commons import (
    opt_api_key,
    opt_base_url,
    opt_config_file,
    opt_json_output_file,
    opt_max_steps,
    opt_model,
    opt_verbose,
)
from GSagent.config import Config
from GSagent.core.agents.runtime import AgentRuntime, run_graph_session
from GSagent.core.eval.dataset import EvalDatasetError, load_dataset
from GSagent.core.eval.metrics import compare_baseline
from GSagent.core.eval.runner import OfflineLLM, load_baseline, run_eval, save_baseline
from GSagent.core.plan import PlanError, merge_plan_results, plan_with_llm
from GSagent.core.policy.audit import AuditLog
from GSagent.core.prompts import build_chat_messages
from GSagent.core.providers.factory import create_chat_model
from GSagent.core.runtime.tasks import DurableTaskManager, queue_db_path
from GSagent.core.history import SnapshotManager
from GSagent.core.history.store import HistoryStore
from GSagent.core.memory import (
    SessionMemoryStore,
    memory_db_path,
    resolve_scope,
    resolve_user_key,
    sessions_dir_path,
)
from GSagent.core.memory.langgraph_store import StoreMemoryAdapter
from GSagent.core.memory.saver import create_store
from GSagent.core.skills.env_info import collect_env_info, format_env_info
from GSagent.core.skills.library import Skill, SkillLibrary
from GSagent.plugins.toolsets.bash.common.cli_prefixes import (
    enable_cli_mode,
    save_cli_bash_tools_approved_prefixes,
)
from GSagent.utils.console import (
    ElapsedSpinner,
    console,
    print_agent,
    print_approval_request,
    print_banner,
    print_error,
    print_hint,
    print_rule,
    print_summary,
    print_tool_result,
    print_user,
    print_warning,
    read_piped_input,
)
from GSagent.utils.file_utils import write_json_file
from GSagent.utils.log import setup_logging
from GSagent.utils.stream import StreamEvents

app = typer.Typer(
    name="GSagent",
    help="General-purpose LLM Agent framework",
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
    no_args_is_help=False,
)

logger = logging.getLogger(__name__)

MUTED_STYLE = "bright_black"

# 企业级 HITL 三态（US1 T015，FR-003，contracts/config.md）
VALID_HITL_MODES = ("auto", "always", "never")


def _validate_hitl(hitl: Optional[str]) -> None:
    """校验 --hitl 取值；非法即抛出 typer.BadParameter。"""
    if hitl is not None and hitl not in VALID_HITL_MODES:
        raise typer.BadParameter(f"--hitl 必须是 {'|'.join(VALID_HITL_MODES)}，收到: {hitl!r}")


def _log_level_for_verbosity(verbose: Optional[List[bool]]) -> Optional[str]:
    """将可重复的 -v 标志映射为日志级别。None 表示使用配置的默认值。"""
    count = len(verbose or [])
    if count >= 2:
        return "DEBUG"
    if count == 1:
        return "INFO"
    return None


def _create_agent(config: Config):
    """根据配置创建单 Agent GraphAgent（新图：ToolNode + 审批下沉 + SqliteSaver）。

    装配失败（如未配置 API Key）→ 打印友好错误并干净退出，不抛未处理 traceback。
    """
    try:
        registry = config.create_tools_registry()
        agent = config.create_single_graph_agent(
            tools_registry=registry,
            checkpointer=config.create_saver(),
            store=config.create_store(),
        )
        return agent, registry
    except RuntimeError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc


def _create_multi_agent(
    config: Config, max_subagents: Optional[int] = None
) -> AgentRuntime:
    """装配多Agent runtime（create_agent 主编排图：decompose → Send 并行）。

    max_subagents: CLI 显式覆盖（`agent chat --max-subagents`），优先于配置。

    装配失败（如未配置 API Key）→ 打印友好错误并干净退出，不抛未处理 traceback。
    """
    if max_subagents is not None and max_subagents <= 0:
        raise typer.BadParameter("--max-subagents 必须是正整数。")
    try:
        return config.create_multi_graph_agent(
            max_subagents=max_subagents,
            checkpointer=config.create_saver(),
            store=config.create_store(),
        )
    except RuntimeError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc


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
    runtime: AgentRuntime,
    messages: List[Dict[str, Any]],
    resume_map: Optional[Dict[str, Dict[str, Any]]],
    session_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """执行一次 run_graph_session 遍历，边接收事件边打印（图消费器）。

    参数:
        runtime: AgentRuntime（编译图 + 横切能力数据包）。
        session_id: 会话 ID，随 request_context 传入以便审计事件按会话聚合
            （US2 T022/T023，FR-005/007）。

    返回:
        (final, pause)：final 是回合完成时的 ANSWER_END 数据；
        当流因等待用户输入而暂停时设置 pause，含 kind（'approval'|'frontend'）、
        pauses（langgraph Interrupt 列表）、messages。
    """
    final: Optional[Dict[str, Any]] = None
    streamed_text = ""
    pauses: List[Any] = []  # langgraph Interrupt 对象

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
        for mode, chunk in run_graph_session(
            runtime,
            messages=messages,
            session_id=session_id or "session",
            resume=resume_map or None,
        ):
            if mode == "updates" and "__interrupt__" in chunk:
                # 审批/前端暂停（per-interrupt-id）：收集全部后一次返回
                stop_spinner()
                pauses.extend(chunk["__interrupt__"])
                continue
            if mode != "custom":
                continue
            etype = chunk["type"]
            data = chunk.get("data") or {}
            if etype == StreamEvents.ANSWER_DELTA:
                # 把流式内容滚进单行转圈里（IDE 伪终端里多行 Live 刷新不可靠）；
                # 完整答案卡片在 ANSWER_END 时才一次性打印。
                streamed_text += data.get("content", "")
                tail = " ".join(streamed_text.split())[-40:]
                ensure_spinner(f"作答中 … {tail}")
            elif etype == StreamEvents.ANSWER_END:
                final = data
                stop_spinner()
                print_agent(
                    data.get("content", ""),
                    elapsed=time.monotonic() - turn_start,
                )
            elif etype == StreamEvents.START_TOOL:
                ensure_spinner(f"正在执行 {data.get('tool_name', '?')} …")
            elif etype == StreamEvents.TOOL_RESULT:
                # 先停掉瞬时转圈再写结果行，rich 才能干净输出
                # （活跃的单行转圈会覆盖 IDE 伪终端里带外的 console.print）。
                stop_spinner()
                print_tool_result(
                    data.get("tool_name", "?"),
                    data.get("status", "?"),
                    data.get("execution_time_ms", 0.0),
                    invocation=data.get("invocation"),
                    return_code=data.get("return_code"),
                )
                ensure_spinner("思考中 …")
            elif etype == StreamEvents.MULTI_AGENT_DECOMPOSE:
                task = data.get("task", "")
                ensure_spinner(f"编排拆解任务 … {task[:40]}")
            elif etype == StreamEvents.MULTI_AGENT_SUBAGENT:
                # 多Agent 事件：子任务明细（records，T017 向后兼容）
                stop_spinner()
                for rec in data.get("records") or []:
                    print_tool_result(
                        f"SubAgent[{rec.get('index', '?')}]",
                        rec.get("kind") or "?",
                        0.0,
                        invocation=rec.get("result"),
                    )
                ensure_spinner("调度中 …")
            elif etype == StreamEvents.MULTI_AGENT_DONE:
                ensure_spinner("多Agent 任务完成 …")
            elif etype == StreamEvents.SUMMARY:
                print_summary(
                    data.get("old_count", "..."),
                    data.get("new_count", "?"),
                )
                ensure_spinner("思考中 …")
            elif etype == StreamEvents.ERROR:
                stop_spinner()
                print_error(data.get("error", "未知错误"))
    finally:
        stop_spinner()

    if pauses:
        return final, {"kind": "pause", "pauses": pauses, "messages": messages}
    return final, None


def _run_turn(
    runtime: AgentRuntime,
    messages: List[Dict[str, Any]],  # 本轮请求的初始消息
    can_prompt: bool,
    session_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """运行整个会话：循环消费流事件，途中解决审批/前端暂停。

    参数:
        runtime: AgentRuntime 实例。
        messages: 本轮请求的初始消息。
        can_prompt: 是否可以向用户交互式询问决策。
        session_id: 会话 ID，透传给 _consume_stream 供审计按会话聚合。

    返回:
        (final, session_history)：final 是 ANSWER_END 数据（若会话从未以答案结束则为 None）；
        session_history 是本次回合累积的会话消息（短期记忆）。
    """
    resume_map: Optional[Dict[str, Dict[str, Any]]] = None
    # 企业级（US2 T023，FR-007/004）：审批决策摘要，附到 final 供 `run --json` 输出。
    approval_summary: Dict[str, int] = {"approved": 0, "denied": 0, "auto_denied": 0}
    # 短期记忆（session_history）：回合中不断累积的会话消息，随审批/前端暂停而更新。
    # 工作记忆只指「单次 LLM 调用实际看到的窗口」，不是整个会话。
    session_history = list(messages)

    while True:
        final, pause = _consume_stream(runtime, session_history, resume_map, session_id)

        if pause is None:
            session_history = final.get("messages") if final else session_history
            if final is not None:
                final["approved"] = approval_summary
            return final, session_history

        session_history = pause["messages"]
        pr_list: List[Any] = pause.get("pauses") or []  # langgraph Interrupt
        resume_map = {}

        for pr in pr_list:
            value = pr.value or {}
            if value.get("type") == "approval":
                # 兼容多 Agent 子图冒泡（pending_approvals 列表）与单 Agent 单请求
                pending: List[Dict[str, Any]] = value.get("pending_approvals") or [value]
                for item in pending:
                    print_approval_request(
                        item.get("tool_name", "?"), item.get("params") or {}
                    )
                    if can_prompt:
                        approved = typer.confirm("  批准该操作？", default=False)
                    else:
                        print_error(
                            "工具需要审批，但 stdin 不是交互终端，已拒绝。"
                            "如需预先放行，请把命令前缀加入 ./.GSagent/config.yaml "
                            "的 `bash.allow` 列表（或设置 builtin_allowlist: extended）。"
                        )
                        approved = False

                    resume_map[pr.id] = {"approved": approved}
                    if approved:
                        approval_summary["approved"] += 1
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
                        if can_prompt:
                            approval_summary["denied"] += 1
                        else:
                            approval_summary["auto_denied"] += 1
            else:  # frontend pause
                print_warning(
                    f"工具 '{value.get('tool_name', '?')}' 正在等待前端执行器，"
                    "但当前 CLI 没有前端，已中止该工具调用。"
                )
                resume_map[pr.id] = {
                    "frontend_tool_results": {
                        "value": "Frontend execution is not available in this environment."
                    }
                }


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
    json_output_file: Optional[str] = opt_json_output_file,
    echo_request: bool = typer.Option(
        True,
        "--echo/--no-echo",
        help="Echo back the question provided to the agent in the output",
    ),
    hitl: Optional[str] = typer.Option(
        None,
        "--hitl",
        help="审批模式: auto|always|never（覆盖 policy.hitl_mode，FR-003）",
    ),
    snapshot: bool = typer.Option(
        False,
        "--snapshot",
        help="任务执行前后自动生成快照（FR-012；配置 policy.snapshot_dir 后默认开启）",
    ),
) -> None:
    """提出一次性问题后退出（支持管道输入 stdin）。"""
    _validate_hitl(hitl)
    log_file = setup_logging(_log_level_for_verbosity(verbose), install_excepthook=True)
    if log_file:
        print_hint(f"日志文件: {log_file}")
    # CLI 模式：加载 CLI 已批准的 bash 前缀，让此前的审批在这里也生效
    enable_cli_mode()
    config = Config(config_path=config_file)
    config.apply_overrides(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_steps=max_steps,
        hitl=hitl,
    )

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

    agent, registry = _create_agent(config)

    messages = build_chat_messages(ask=prompt, toolsets=registry.get_all_tools())

    # 只有 stdin 是真实终端时才能交互式审批
    # （管道输入此刻已被消费完毕）。
    can_prompt = sys.stdin.isatty() and not piped_data
    # 企业级（US2 T022）：会话 ID 随 request_context 进审计，供 history usage 聚合。
    session_id = f"run-{uuid.uuid4().hex[:8]}"
    # FR-012：任务执行前后自动生成快照（配置 policy.snapshot_dir 或 --snapshot）。
    snap_mgr = _snapshot_manager_from_config(config) if snapshot else None
    workspace_root = _workspace_root_from_config(config)
    pre_snapshot: Optional[Dict[str, Any]] = None
    final: Optional[Dict[str, Any]] = None
    try:
        if snap_mgr is not None:
            pre_snapshot = snap_mgr.create_workspace_snapshot(workspace_root)
            print_hint(f"已生成执行前工作区快照 {pre_snapshot['snapshot_id']}（FR-012）")
        final, session_history = _run_turn(
            agent, messages, can_prompt=can_prompt, session_id=session_id
        )
        if snap_mgr is not None:
            post = snap_mgr.create_session_snapshot(session_id, session_history)
            print_hint(f"已生成执行后会话快照 {post['snapshot_id']}（FR-012）")
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


def _run_plan_turn(
    runtime: AgentRuntime,
    user_input: str,
    session_id: str,
    can_prompt: bool = False,
) -> Optional[str]:
    """Plan-and-Execute 单回合（US3 T028，FR-008，contracts/cli.md）。

    走 Plan runtime（规划 → Send 批次并行 → 归并），产出 PLAN/PLAN_TASK/
    ANSWER_END 事件（CLI 消费 langgraph custom 流）。返回归并文本；失败返回 None。
    """
    messages = build_chat_messages(ask=user_input, toolsets=[])
    final, _ = _run_turn(agent, messages, can_prompt=can_prompt, session_id=session_id)
    if final is None:
        return None
    summary = final.get("content") or ""
    print_agent(summary)
    return summary


@dataclass
class _ChatCtx:
    """聊天循环上下文：斜杠命令处理器所需的一切（CLI 层，宪法 II 只消费事件流）。

    memory_store：长期记忆（SQLite memory_store，跨会话、按 scope）；
    session_memory：会话记忆目录（session_history 落盘）；
    skill_library：技能库（/skill 展示）。
    """

    agent: Any
    toolsets: List[Any]
    session_id: str = ""
    plan_mode: bool = False
    plan_agent: Any = None
    memory_store: Optional[StoreMemoryAdapter] = None
    memory_scope: str = ""
    memory_user: str = ""
    session_memory: Optional[SessionMemoryStore] = None
    skill_library: Optional[SkillLibrary] = None
    session_mode: str = "chat"


def _print_tools(tools: List[Any]) -> None:
    """列出可用工具（/tool，langchain @tool 注册表）。"""
    table = Table(title="可用工具")
    table.add_column("工具", style="bold")
    table.add_column("说明")
    for tool in tools:
        table.add_row(tool.name, (getattr(tool, "description", "") or "")[:80])
    console.print(table)
    if not tools:
        print_hint("暂无可用工具。")


def _print_memory(ctx: "_ChatCtx", query: str = "") -> None:
    """打印会话信息 + 当前 scope 的长期记忆（/memory [query]）。"""
    print_rule("会话信息")
    info = [f"session_id: {ctx.session_id}", f"模式: {ctx.session_mode}"]
    if ctx.session_memory is not None:
        info.append(f"记忆目录: {ctx.session_memory.root}")
        _, hist = ctx.session_memory.load(ctx.session_id)
        if hist is not None:
            info.append(f"消息数: {len(hist)}")
    console.print("  " + "\n  ".join(info))

    if ctx.memory_store is None:
        print_hint("长期记忆未启用（缺少 memory_store）。")
        return
    scope = ctx.memory_scope
    user = ctx.memory_user
    if query:
        rows = ctx.memory_store.search(scope, query, limit=10, user=user)
        title = f"长期记忆搜索（scope={scope}，user={user}，query='{query}'）"
    else:
        stats = ctx.memory_store.stats(scope, user=user)
        rows = ctx.memory_store.list(scope, limit=10, user=user)
        title = f"最近长期记忆（scope={scope}，user={user}，共 {stats['total']} 条）"
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("类型", style="bold")
    table.add_column("来源")
    table.add_column("时间", style=MUTED_STYLE)
    table.add_column("内容")
    for r in rows:
        table.add_row(str(r["id"]), r["kind"], r["source"], r["updated_at"], r["content"])
    console.print(table)
    if not rows:
        print_hint("暂无长期记忆。可用 `/remember <内容>` 保存，或让 agent 调用 remember 工具。")


def _handle_chat_slash(user_input: str, ctx: "_ChatCtx") -> Optional[str]:
    """处理聊天斜杠命令。

    返回：
        None  → 非斜杠命令，继续走正常 agent 回合；
        "exit" → 已处理且会话应退出（调用方 break）；
        其他字符串 → 已处理，继续循环。
    """
    low = user_input.lower()

    # 退出
    if low in ("/exit", "/quit", "exit", "quit"):
        console.print("[bold magenta]再见！[/bold magenta]")
        return "exit"

    # /hitl <mode>：运行时切换审批模式（FR-003）
    if low.startswith("/hitl"):
        parts = user_input.split()
        if len(parts) != 2 or parts[1] not in VALID_HITL_MODES:
            print_error(f"用法: /hitl {'|'.join(VALID_HITL_MODES)}")
            return "ok"
        if hasattr(ctx.agent, "set_hitl_mode"):
            try:
                ctx.agent.set_hitl_mode(parts[1])
                print_hint(f"审批模式已切换为 {parts[1]}")
            except Exception as e:  # noqa: BLE001 - CLI 层容错
                print_error(f"切换审批模式失败: {e}")
        else:
            print_warning("当前模式（多Agent 编排）暂不支持 /hitl 切换。")
        return "ok"

    # /remember <content>：手动写入长期记忆（source=manual）
    if low.startswith("/remember"):
        if ctx.memory_store is None:
            print_error("长期记忆未启用（缺少 memory_store）。")
            return "ok"
        content = user_input[len("/remember"):].strip()
        if not content:
            print_error("用法: /remember <记忆内容>")
            return "ok"
        try:
            rec = ctx.memory_store.remember(
                scope=ctx.memory_scope,
                content=content,
                kind="fact",
                source="manual",
                user=ctx.memory_user,
            )
        except ValueError as e:
            print_error(f"保存记忆失败: {e}")
            return "ok"
        status = "已保存" if rec["access_count"] == 0 else "已强化（去重）"
        print_hint(f"[长期记忆] {status} #{rec['id']}（scope={ctx.memory_scope}）")
        return "ok"

    # /memory [query]：查看会话信息 + 长期记忆
    if low.startswith("/memory"):
        _print_memory(ctx, query=user_input[len("/memory"):].strip())
        return "ok"

    # /tool：查看可用工具
    if low == "/tool":
        _print_tools(ctx.toolsets)
        return "ok"

    # /skill：查看技能库
    if low == "/skill":
        if ctx.skill_library is None:
            print_warning("技能库未加载（skill_library 为空）。")
        else:
            _print_skills(ctx.skill_library)
        return "ok"

    return None


def _chat_loop(
    agent: Any,
    toolsets: List[Any],
    plan_mode: bool = False,
    plan_agent: Any = None,
    memory_store: Optional[StoreMemoryAdapter] = None,
    session_memory: Optional[SessionMemoryStore] = None,
    skill_library: Optional[SkillLibrary] = None,
    memory_scope: str = "",
    memory_user: str = "",
    session_mode: str = "chat",
) -> None:
    """交互式聊天主循环：每轮消费 GraphAgent.stream()（单/多Agent/Plan 通用）。

    `agent`：GraphAgent（单 Agent 或主编排图），产出 StreamMessage + PauseRequest。

    plan_mode：Plan-and-Execute（US3 T028）——每回合独立规划 DAG 并按依赖批次
    并行执行（经 `plan_agent`），确定性执行路径，不累积会话短期记忆。
    """
    session_history: Optional[List[Dict[str, Any]]] = None
    # 企业级（US2 T022）：整个交互会话共用一个会话 ID（审计按会话聚合）。
    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    ctx = _ChatCtx(
        agent=agent,
        toolsets=toolsets,
        session_id=session_id,
        plan_mode=plan_mode,
        plan_agent=plan_agent,
        memory_store=memory_store,
        memory_scope=memory_scope,
        memory_user=memory_user,
        session_memory=session_memory,
        skill_library=skill_library,
        session_mode=session_mode,
    )

    def _persist() -> None:
        """把当前 session_history 落盘到会话记忆目录（失败静默降级）。"""
        if ctx.session_memory is not None:
            ctx.session_memory.save(
                session_id, session_history or [], meta={"mode": ctx.session_mode}
            )

    while True:
        try:
            console.print("[bold cyan]你 >[/bold cyan]", end=" ")
            user_input = input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold magenta]再见！[/bold magenta]")
            _persist()
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # 斜杠命令：/exit /quit /hitl /memory /tool /skill /remember
        result = _handle_chat_slash(user_input, ctx)
        if result == "exit":
            _persist()
            break
        if result is not None:
            continue

        # US3 T028：plan 模式——确定性 DAG 规划 + 按依赖批次并行执行
        # （独立于 ReAct 主循环；每回合不累积短期记忆，故不落盘）。
        if ctx.plan_mode and ctx.plan_agent is not None:
            try:
                _run_plan_turn(ctx.plan_agent, user_input, session_id)
            except Exception as e:  # noqa: BLE001 - CLI 层容错
                logger.debug("Plan turn failed", exc_info=True)
                print_error(f"Plan 回合失败: {e}")
            continue

        messages = build_chat_messages(
            ask=user_input,
            session_history=session_history,
            toolsets=ctx.toolsets,
        )

        try:
            # 每轮回合结束，会话的短期记忆随返回的 session_history 更新
            final, session_history = _run_turn(
                ctx.agent, messages, can_prompt=True, session_id=session_id
            )
        except KeyboardInterrupt:
            print_error("已中断。")
        except Exception as e:
            logger.debug("Agent turn failed", exc_info=True)
            print_error(f"Agent 回合失败: {e}")
        # 会话记忆落盘：每轮结束把 session_history 写到 <会话记忆目录>/
        _persist()


@app.command()
def chat(
    # 通用选项
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    base_url: Optional[str] = opt_base_url,
    config_file: Optional[Path] = opt_config_file,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose, #
    multi_agent: bool = typer.Option(
        False,
        "--multi-agent",
        help="启用多Agent 编排模式（主/编排/业务/SubAgent 协作，contracts/cli.md）",
    ),
    max_subagents: Optional[int] = typer.Option(
        None,
        "--max-subagents",
        help="多Agent 单任务最大并行 SubAgent 数（覆盖 multi_agent.max_subagents）",
    ),
    hitl: Optional[str] = typer.Option(
        None,
        "--hitl",
        help="审批模式: auto|always|never（覆盖 policy.hitl_mode；交互内可用 /hitl 切换，FR-003）",
    ),
    plan: bool = typer.Option(
        False,
        "--plan",
        help="Plan-and-Execute：先产出任务 DAG，按依赖批次并行执行（FR-008，contracts/cli.md）",
    ),
) -> None:
    """与 agent 开始交互式聊天会话（默认命令）。"""
    _validate_hitl(hitl)
    log_file = setup_logging(_log_level_for_verbosity(verbose), install_excepthook=True)
    if log_file:
        print_hint(f"日志文件: {log_file}")
    # CLI 模式：从 ./.GSagent/bash_approved_prefixes.yaml 加载 CLI 已批准的 bash 前缀
    enable_cli_mode()
    config = Config(config_path=config_file)
    config.apply_overrides(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_steps=max_steps,
        hitl=hitl,
    )

    # 记忆系统装配：长期记忆（SQLite memory_store）+ 会话记忆目录（session_history
    # 落盘）+ 技能库——供斜杠命令 /memory /tool /skill /remember 使用。
    # 用户维度（004-memory-isolation）：config memory.user（空=自动系统用户）
    memory_user = resolve_user_key(
        str((config.data.get("memory") or {}).get("user") or "")
    )
    memory_store = StoreMemoryAdapter(
        create_store(memory_db_path(config)),
        max_entries=int((config.data.get("memory") or {}).get("max_entries") or 500),
    )
    session_memory = SessionMemoryStore(root=sessions_dir_path(config), user=memory_user)
    skill_library = SkillLibrary()
    memory_scope = resolve_scope(
        str((config.data.get("memory") or {}).get("scope") or "")
    )

    # 多Agent 开关：`--multi-agent` 强制开启；否则由配置 multi_agent.enabled 决定。
    use_multi_agent = multi_agent or bool(
        config.multi_agent_settings().get("enabled", False)
    )

    # Plan-and-Execute（US3 T028，FR-008）：确定性 DAG 执行，独立于多Agent 拆解。
    if plan:
        if use_multi_agent:
            print_hint(
                "plan 模式接管：--multi-agent/配置拆解被忽略，改用确定性 DAG 规划。\n"
            )
        plan_agent = config.create_plan_graph_agent(
            checkpointer=config.create_saver(),
            store=config.create_store(),
            max_subagents=max_subagents,
        )
        print_banner(
            model=config.data["llm"]["model"],
            tool_count=len(plan_agent.tools_registry),
        )
        print_hint(
            "Plan-and-Execute 模式：先规划任务 DAG，再按依赖批次并行执行。"
            "输入你的问题，'/exit' 退出。\n"
        )
        _chat_loop(
            plan_agent,
            plan_agent.tools_registry.get_all_tools(),
            plan_mode=True,
            plan_agent=plan_agent,
            memory_store=memory_store,
            memory_scope=memory_scope,
            memory_user=memory_user,
            session_memory=session_memory,
            skill_library=skill_library,
        )
        return

    if use_multi_agent:
        if not multi_agent:
            print_hint(
                "multi_agent.enabled=true 已自动启用多Agent 编排模式"
                "（可用 agent chat --multi-agent 显式指定）。\n"
            )
        main_agent = _create_multi_agent(config, max_subagents=max_subagents)
        registry = main_agent.tools_registry
        print_banner(
            model=config.data["llm"]["model"],
            tool_count=len(registry) if registry else 0,
        )
        print_hint(
            "多Agent 编排模式：主/编排/业务/SubAgent 协作。"
            "输入你的问题，'/exit' 退出。\n"
        )
        _chat_loop(
            main_agent,
            registry.get_all_tools() if registry else [],
            memory_store=memory_store,
            memory_scope=memory_scope,
            memory_user=memory_user,
            session_memory=session_memory,
            skill_library=skill_library,
            session_mode="multi_agent",
        )
        return

    agent, registry = _create_agent(config)

    print_banner(
        model=config.data["llm"]["model"],
        tool_count=len(registry),
    )
    print_hint("输入你的问题，'/exit' 或 Ctrl+C 退出。\n")

    _chat_loop(
        agent,
        registry.get_all_tools(),
        memory_store=memory_store,
        memory_scope=memory_scope,
        memory_user=memory_user,
        session_memory=session_memory,
        skill_library=skill_library,
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Server host"),
    port: Optional[int] = typer.Option(None, "--port", help="Server port（默认 runtime.serve_port）"),
    hitl: Optional[str] = typer.Option(
        None,
        "--hitl",
        help="审批模式: never|auto（默认 never——服务无审批界面，危险操作拒绝，FR-004/011）",
    ),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """启动 Runtime API（线程/回合/SSE + 持久化后台任务，US4 FR-011）。

    工具标签过滤 [CORE, CLUSTER]（排除仅 CLI 专用工具集）。
    serve 与 CLI 一样只是 StreamMessage 事件流消费者（宪法 II）。
    """
    if hitl is not None and hitl not in ("never", "auto"):
        raise typer.BadParameter("serve 仅支持 --hitl never|auto（服务无人类审批界面）。")
    config = Config(config_path=config_file)
    mode = hitl or (config.data.get("policy") or {}).get("hitl_mode", "never")
    if mode == "always":
        raise typer.BadParameter(
            "serve 不能使用 hitl=always（服务无人类审批界面）；请用 never|auto。"
        )
    serve_port = port or (config.data.get("runtime") or {}).get("serve_port", 8000)

    # 懒加载：fastapi/uvicorn 是可选依赖（[project.optional-dependencies] server），
    # 未安装时仅 `agent serve` 报错，其余命令不受影响。
    from GSagent.core.runtime.server import create_app
    import uvicorn

    try:
        app = create_app(config=config, hitl_mode=mode)
    except RuntimeError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc
    print_hint(f"Runtime API 监听 {host}:{serve_port}（审批模式 {mode}）")
    uvicorn.run(app, host=host, port=int(serve_port))


@app.command()
def toolset(
    config_file: Optional[Path] = opt_config_file,
    verbose: Optional[List[bool]] = opt_verbose,
) -> None:
    """列出可用的 langchain 工具（@tool 注册表，002-langchain-ecosystem）。"""
    setup_logging(_log_level_for_verbosity(verbose))
    config = Config(config_path=config_file)
    # 这里不需要 LLM —— 只列出工具注册表。
    registry = config.create_tools_registry()
    tools = registry.get_all_tools()

    table = Table(title="工具集", show_lines=False)
    table.add_column("工具", style="bold")
    table.add_column("说明")
    table.add_column("参数", style=MUTED_STYLE)

    for t in tools:
        args = getattr(t, "args", None) or {}
        param_names = ", ".join(args.keys()) if args else "-"
        table.add_row(
            t.name,
            (getattr(t, "description", "") or "")[:60],
            param_names,
        )

    console.print(table)
    console.print(f"[bright_black]已加载 {len(tools)} 个工具[/bright_black]")


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


def _audit_log_from_config(config_file: Optional[Path]) -> AuditLog:
    """从配置解析审计日志路径（policy.audit_dir，缺省 ./.GSagent/audit）。

    供 `history usage` / `run --json` 用量聚合使用（US2 T022/T023，FR-007）。
    """
    config = Config(config_path=config_file)
    policy = config.data.get("policy") or {}
    audit_dir = policy.get("audit_dir") or ""
    if audit_dir:
        return AuditLog(path=Path(audit_dir).expanduser() / "audit.jsonl")
    return AuditLog()


@history_app.command("usage")
def history_usage(
    session_id: Optional[str] = typer.Option(
        None, "--session-id", help="只聚合指定会话的用量"
    ),
    limit: int = typer.Option(
        100, "--limit", help="最多聚合的 model_call 事件条数（从最新往前）"
    ),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """按会话聚合模型用量与估算成本（FR-007，SC-004，数据源 audit model_call）。"""
    audit = _audit_log_from_config(config_file)
    events = audit.tail(limit=limit, session_id=session_id, event_type="model_call")
    if not events:
        print_hint("没有可聚合的用量记录（audit model_call 事件为空）。")
        return

    # 按会话聚合 token + 估算成本；同一会话可含多个模型（各带 usage.model）
    rows: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        sid = ev.get("session_id") or "(无会话)"
        usage = ev.get("usage") or {}
        acc = rows.setdefault(
            sid,
            {
                "model_calls": 0,
                "models": set(),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost": 0.0,
            },
        )
        acc["model_calls"] += 1
        model = usage.get("model") or "?"
        acc["models"].add(model)
        acc["prompt_tokens"] += usage.get("prompt_tokens", 0)
        acc["completion_tokens"] += usage.get("completion_tokens", 0)
        acc["total_tokens"] += usage.get("total_tokens", 0)
        acc["estimated_cost"] += usage.get("estimated_cost", 0) or 0

    table = Table(title="用量/成本聚合（audit model_call）")
    table.add_column("会话", style="bold")
    table.add_column("调用数")
    table.add_column("模型")
    table.add_column("Prompt", style=MUTED_STYLE)
    table.add_column("Completion", style=MUTED_STYLE)
    table.add_column("Total")
    table.add_column("估算成本($)")
    for sid, acc in rows.items():
        table.add_row(
            sid,
            str(acc["model_calls"]),
            ", ".join(sorted(acc["models"])),
            str(acc["prompt_tokens"]),
            str(acc["completion_tokens"]),
            str(acc["total_tokens"]),
            f"{acc['estimated_cost']:.6f}",
        )
    console.print(table)


app.add_typer(history_app)


# ======================= US4：后台任务查询/取消（FR-009/010，contracts/cli.md） =======================
tasks_app = typer.Typer(
    name="tasks",
    help="后台任务查询/取消（投递走 Runtime API，CLI 只做管理视图，US4）",
    no_args_is_help=True,
)


def _task_manager_from_config(config_file: Optional[Path]) -> DurableTaskManager:
    """从配置解析任务队列路径并打开 manager（runtime.queue_db，空=./.GSagent/runtime.db）。"""
    return DurableTaskManager(path=queue_db_path(Config(config_path=config_file)))


@tasks_app.command("list")
def tasks_list(
    scope: Optional[str] = typer.Option(None, "--scope", help="按项目目录过滤"),
    state: Optional[str] = typer.Option(None, "--state", help="按状态过滤（queued/running/completed/failed/canceled）"),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """列出后台任务（FR-009，per-scope 视图）。"""
    records = _task_manager_from_config(config_file).list(scope=scope, state=state)
    table = Table(title="后台任务")
    table.add_column("任务 ID", style="bold")
    table.add_column("scope")
    table.add_column("状态", style=MUTED_STYLE)
    table.add_column("创建时间", style=MUTED_STYLE)
    for rec in records:
        table.add_row(
            rec["id"],
            rec["scope"],
            rec["state"],
            rec["created_at"],
        )
    console.print(table)
    if not records:
        print_hint("没有匹配的后台任务。")


@tasks_app.command("cancel")
def tasks_cancel(
    task_id: str = typer.Argument(..., help="任务 ID"),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """取消后台任务（canceled 优先，迟到结果不覆盖，FR-010）。"""
    if _task_manager_from_config(config_file).cancel(task_id):
        console.print(f"[green]已取消[/green] {task_id}")
    else:
        print_error(f"任务不可取消（可能已终止）: {task_id}")
        raise typer.Exit(code=1)


app.add_typer(tasks_app)


# ======================= US5：快照（FR-012，contracts/cli.md） =======================
snapshot_app = typer.Typer(
    name="snapshot",
    help="会话/工作区快照的查询与恢复（US5，FR-012）",
    no_args_is_help=True,
)


def _snapshot_dir_from_config(config_file: Optional[Path]) -> Path:
    """快照目录：policy.snapshot_dir，空=./.GSagent/snapshots（B：单一路径来源）。"""
    d = str(
        (Config(config_path=config_file).data.get("policy") or {}).get("snapshot_dir") or ""
    )
    return Path(d).expanduser() if d else DEFAULT_SNAPSHOT_DIR


def _snapshot_manager_from_config(config: Config) -> Optional[SnapshotManager]:
    """FR-012：policy.snapshot_dir 配置后返回 SnapshotManager，否则 None
    （`agent run --snapshot` 与配置了快照目录时自动开启执行前后快照）。"""
    d = str((config.data.get("policy") or {}).get("snapshot_dir") or "")
    return SnapshotManager(Path(d).expanduser()) if d else None


def _workspace_root_from_config(config: Config) -> Path:
    """工作区根：policy.workspace_root，空=当前目录（FR-001）。"""
    w = str((config.data.get("policy") or {}).get("workspace_root") or "")
    return Path(w).expanduser() if w else Path.cwd()


@snapshot_app.command("list")
def snapshot_list(
    kind: Optional[str] = typer.Option(None, "--kind", help="session|workspace"),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """列出快照（最新在前）。"""
    snaps = SnapshotManager(_snapshot_dir_from_config(config_file)).list_snapshots(kind=kind)
    table = Table(title="快照")
    table.add_column("快照 ID", style="bold")
    table.add_column("类型", style=MUTED_STYLE)
    table.add_column("时间", style=MUTED_STYLE)
    table.add_column("会话")
    for s in snaps:
        table.add_row(s["snapshot_id"], s["kind"], s["ts"], s.get("session_id", "-"))
    console.print(table)
    if not snaps:
        print_hint("没有快照。")


@snapshot_app.command("restore")
def snapshot_restore(
    snapshot_id: str = typer.Argument(..., help="快照 ID（agent snapshot list 查看）"),
    workspace_root: Optional[Path] = typer.Option(
        None, "--workspace", help="工作区根路径（workspace 快照校验目标，FR-012）"
    ),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """恢复快照：session 重建会话；workspace 对照工作区校验清单。"""
    try:
        out = SnapshotManager(_snapshot_dir_from_config(config_file)).restore(
            snapshot_id, workspace_root=workspace_root
        )
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)
    if out["kind"] == "session":
        console.print(
            f"[green]已恢复会话[/green] {out['session_id']}（{len(out['messages'])} 条消息）"
        )
        print_hint(f"会话指纹: {out['session_fingerprint'][:16]}…")
    else:
        if out["status"] == "ok":
            console.print(f"[green]工作区清单校验通过[/green] {snapshot_id}")
        else:
            print_warning(f"工作区有 {len(out['mismatched'])} 处变更:")
            for rel in out["mismatched"]:
                console.print(f"  [red]~[/red] {rel}")


app.add_typer(snapshot_app)


# ======================= US5：评估（FR-013，contracts/cli.md） =======================
eval_app = typer.Typer(
    name="eval",
    help="评估体系：跑评估/建立基线/列出数据集与基线（US5，FR-013）",
    no_args_is_help=True,
)


def _eval_dirs_from_config(config_file: Optional[Path]) -> Tuple[Path, Path]:
    """评估目录：eval.datasets_dir / eval.baseline_dir（B：单一路径来源）。"""
    e = Config(config_path=config_file).data.get("eval") or {}
    datasets_dir = Path(str(e.get("datasets_dir") or "eval/datasets")).expanduser()
    baseline_dir = Path(str(e.get("baseline_dir") or "eval/baselines")).expanduser()
    return datasets_dir, baseline_dir


def _resolve_dataset(datasets_dir: Path, dataset: str) -> Path:
    """数据集参数 → 路径：显式文件直接用，否则在 datasets_dir 下按名解析。"""
    p = Path(dataset)
    if p.is_file():
        return p
    for cand in (
        datasets_dir / dataset,
        datasets_dir / f"{dataset}.yaml",
        datasets_dir / dataset / "dataset.yaml",
    ):
        if cand.is_file():
            return cand
    raise typer.BadParameter(f"数据集不存在: {dataset}（在 {datasets_dir} 下查找）")


def _print_eval_report(
    report: Dict[str, Any],
    *,
    baseline: Optional[Dict[str, Any]] = None,
    regressions: Optional[List[str]] = None,
) -> None:
    """打印评估报告（指标 + 基线对比 + 错误案例回流）。"""
    m = report["metrics"]
    console.print(f"run_id: [bold]{report['run_id']}[/bold]  案例数: {report['total']}")

    def row(label: str, value: float) -> str:
        base = baseline.get(label) if baseline else None
        if base is not None:
            return f"{value * 100:.1f}%  (基线 {base * 100:.1f}%)"
        return f"{value * 100:.1f}%"

    table = Table(title="评估指标")
    table.add_column("指标", style="bold")
    table.add_column("值")
    table.add_row("完成率", row("completion_rate", m["completion_rate"]))
    table.add_row("幻觉率", row("hallucination_rate", m["hallucination_rate"]))
    table.add_row("误拒绝率", row("false_reject_rate", m["false_reject_rate"]))
    console.print(table)

    if baseline is None:
        print_hint("无基线，可用 `agent eval baseline` 建立（SC-007）。")
    elif regressions:
        print_warning("相对基线存在能力退化:")
        for r in regressions:
            console.print(f"  [red]![/red] {r}")
    else:
        console.print("[green]相对基线无退化[/green] (SC-007)")

    review = m.get("review_cases", [])
    if review:
        console.print(f"[yellow]错误案例回流 {len(review)} 条（供专家评审）:[/yellow]")
        for r in review[:10]:
            console.print(f"  {r['case_id']}  ({r['verdict']})")
        if len(review) > 10:
            console.print(f"  … 其余 {len(review) - 10} 条")


@eval_app.command("run")
def eval_run(
    dataset: str = typer.Argument(..., help="数据集路径或名称（eval.datasets_dir 下）"),
    baseline: Optional[str] = typer.Option(
        None, "--baseline", help="对比基线名（默认取数据集同名基线）"
    ),
    use_llm: bool = typer.Option(
        False, "--llm", help="用真实模型（默认 OfflineLLM 离线回归冒烟）"
    ),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """跑评估并输出三指标 + 基线对比（FR-013/SC-007）。"""
    datasets_dir, baseline_dir = _eval_dirs_from_config(config_file)
    path = _resolve_dataset(datasets_dir, dataset)
    try:
        cases = load_dataset(path)
    except EvalDatasetError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)

    config = Config(config_path=config_file)
    if use_llm:
        try:
            llm = create_chat_model(config.data.get("llm"))
        except RuntimeError as exc:
            print_error(str(exc))
            raise typer.Exit(code=1) from exc
    else:
        llm = OfflineLLM()
    report = run_eval(cases, llm)

    baseline_name = baseline or path.stem
    base = load_baseline(baseline_dir / f"{baseline_name}.json")
    regressions = compare_baseline(report["metrics"], base) if base else None
    _print_eval_report(report, baseline=base, regressions=regressions)


@eval_app.command("baseline")
def eval_baseline(
    dataset: str = typer.Argument(..., help="数据集路径或名称"),
    name: Optional[str] = typer.Option(None, "--name", help="基线名（默认数据集同名）"),
    use_llm: bool = typer.Option(
        False, "--llm", help="用真实模型建基线（默认 OfflineLLM 离线）"
    ),
    config_file: Optional[Path] = opt_config_file,
) -> None:
    """建立/更新基线（SC-007：发版回归对比依据）。"""
    datasets_dir, baseline_dir = _eval_dirs_from_config(config_file)
    path = _resolve_dataset(datasets_dir, dataset)
    try:
        cases = load_dataset(path)
    except EvalDatasetError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)

    config = Config(config_path=config_file)
    if use_llm:
        try:
            llm = create_chat_model(config.data.get("llm"))
        except RuntimeError as exc:
            print_error(str(exc))
            raise typer.Exit(code=1) from exc
    else:
        llm = OfflineLLM()
    report = run_eval(cases, llm)

    baseline_name = name or path.stem
    target = baseline_dir / f"{baseline_name}.json"
    save_baseline(target, report["metrics"])
    console.print(f"[green]已建立/更新基线[/green] {target}")
    _print_eval_report(report)


@eval_app.command("list")
def eval_list(config_file: Optional[Path] = opt_config_file) -> None:
    """列出数据集与基线（FR-013）。"""
    datasets_dir, baseline_dir = _eval_dirs_from_config(config_file)
    datasets = sorted(datasets_dir.glob("*.yaml")) if datasets_dir.is_dir() else []
    baselines = sorted(baseline_dir.glob("*.json")) if baseline_dir.is_dir() else []

    table = Table(title="评估数据集")
    table.add_column("数据集", style="bold")
    table.add_column("案例数")
    for d in datasets:
        try:
            count = len(load_dataset(d))
        except EvalDatasetError:
            count = "-"
        table.add_row(d.name, str(count))
    console.print(table)

    table2 = Table(title="基线")
    table2.add_column("基线", style="bold")
    table2.add_column("完成率")
    table2.add_column("幻觉率")
    table2.add_column("误拒绝率")
    for b in baselines:
        m = load_baseline(b) or {}
        table2.add_row(
            b.stem,
            f"{m.get('completion_rate', 0) * 100:.1f}%",
            f"{m.get('hallucination_rate', 0) * 100:.1f}%",
            f"{m.get('false_reject_rate', 0) * 100:.1f}%",
        )
    console.print(table2)
    if not datasets:
        print_hint("没有数据集（eval.datasets_dir 下 *.yaml）。")
    if not baselines:
        print_hint("没有基线。")


app.add_typer(eval_app)


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
