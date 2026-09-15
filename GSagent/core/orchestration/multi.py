"""多 Agent 主编排图（纯 langgraph 重构，Phase 3）。

替换旧 A2A + ThreadPoolExecutor 编排：
``START → decompose → (Send) → business/command worker(create_agent 子图) → gather → finalize → END``

- ``decompose``：LLM/启发式拆解子任务（复用旧 Orchestrator 逻辑，落为纯函数）。
- ``dispatch`` 条件边：按 kind 用 ``Send`` 原生并行分发到 worker 子图节点。
- ``business_worker`` / ``command_worker``：create_agent 官方子图（interrupt
  自动冒泡父图，CLI 单层 resume，langgraph 自动路由）。
- ``gather``：从父图 messages 通道收集各子图 AI 结果并归并。
- ``finalize``：终局归纳 LLM 生成最终答复（ANSWER_END 事件）。

事件（_stream_messages）：MULTI_AGENT_DECOMPOSE / MULTI_AGENT_SUBAGENT /
MULTI_AGENT_DONE / ANSWER_DELTA / ANSWER_END / USAGE。
"""

import json
import logging
import re
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send

from GSagent.core.llm_adapter import messages_to_dict
from GSagent.core.observability import AgentEventType
from GSagent.core.orchestration.nodes import _emit, _msg
from GSagent.core.orchestration.state import _append_list
from GSagent.core.prompts import build_chat_messages
from GSagent.utils.stream import StreamEvents

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = (
    "你是多Agent 系统中的编排 Agent。请把用户任务拆解为若干可并行子任务。"
    "只输出 JSON 数组，每个元素形如 "
    '{"kind": "command"|"business", "text": "子任务内容"}。'
    "命令类子任务(kind=command)的 text 必须是操作系统可直接执行的命令字符串"
    "（如 ls -la、Get-ChildItem -Path ...），绝不能是描述性伪命令；命令要带完整"
    "参数，直接给目标命令本身，不要用 `powershell -Command ...` / `bash -c ...`"
    "之类的外壳包装。"
    "业务/分析类子任务(kind=business)的内容是待分析的领域问题。"
    "不要输出任何其他文字。"
)

MAIN_SYSTEM_PROMPT = (
    "你是多Agent 系统的用户主接口。请把编排层返回的各子任务结果，"
    "归纳为一条面向用户的、结构化自然语言最终答复。"
)

_SPLIT_RE = re.compile(r"\s*(?:\&\&|;|\n)\s*")

# 含命令包装/终端程序（powershell/cmd/bash/wsl）：LLM 拆解时常产出
# "powershell -Command ..." 这类包装命令，首词识别后即可交给命令工具执行。
SHELL_COMMAND_PREFIXES = frozenset(
    {
        "df", "du", "ls", "dir", "cat", "echo", "pwd", "whoami", "uname",
        "date", "grep", "head", "tail", "sort", "uniq", "wc", "cut", "tr",
        "id", "hostname", "which", "type", "git", "kubectl", "find", "stat",
        "base64", "mkdir", "rm", "touch", "cd", "python",
        "get-childitem", "get-content", "write-output",
        "powershell", "pwsh", "cmd", "bash", "wsl",
    }
)


class MultiGraphState(TypedDict, total=False):
    """多 Agent 主编排图状态。"""

    # 对话消息：用户输入 + 各 worker 子图 merge 回来的结果消息
    messages: Annotated[list[BaseMessage], add_messages]
    # decompose 输出子任务列表 [{kind, text}]
    subtasks: List[Dict[str, Any]]
    request_context: Optional[Dict[str, Any]]
    # 事件缓冲（append reducer，前缀 _：编排内部）
    _stream_messages: Annotated[List[Dict[str, Any]], _append_list]


# ---- 拆解（纯函数，复用旧 Orchestrator 逻辑）----
def plan_with_llm(llm: Any, text: str, system_prompt: str = ORCHESTRATOR_SYSTEM_PROMPT) -> List[Dict[str, str]]:
    """LLM 拆解：返回 [{kind, text}]；失败/无结果返回 []（调用方回退启发式）。"""
    try:
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=text or "(空任务)"),
            ]
        )
        content = (getattr(response, "content", "") or "").strip()
        start, end = content.find("["), content.rfind("]")
        if start == -1 or end == -1:
            return []
        data = json.loads(content[start : end + 1])
        return [
            {
                "kind": "command" if str(item.get("kind", "business")) == "command" else "business",
                "text": str(item.get("text", "")),
            }
            for item in data
            if isinstance(item, dict) and item.get("text")
        ]
    except Exception as exc:  # noqa: BLE001 - 降级启发式
        logger.warning("Orchestrator LLM 拆解失败，回退启发式: %s", exc)
        return []


def decompose_heuristic(text: str) -> List[Dict[str, str]]:
    """确定性拆解：按 && / ; / 换行 切分命令；其余归为业务子任务。"""
    subtasks: List[Dict[str, str]] = []
    for part in _SPLIT_RE.split(text or ""):
        stripped = part.strip()
        if not stripped:
            continue
        first = stripped.split()[0].lower()
        if first in SHELL_COMMAND_PREFIXES:
            subtasks.append({"kind": "command", "text": stripped})
        else:
            subtasks.append({"kind": "business", "text": stripped})
    return subtasks


# ---- 节点工厂 ----
def decompose_node_factory(
    orchestrator_llm: Any,
    *,
    system_prompt: str = ORCHESTRATOR_SYSTEM_PROMPT,
    knowledge_text: str = "",
) -> Any:
    """decompose 节点：拆解用户任务 → subtasks，产 MULTI_AGENT_DECOMPOSE 事件。"""

    def _decompose(state: Dict[str, Any]) -> Dict[str, Any]:
        messages: list[BaseMessage] = list(state.get("messages") or [])
        user_text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                user_text = str(m.content or "")
                break
        prompt = system_prompt
        if knowledge_text:
            prompt += "\n\n" + knowledge_text
        subtasks = plan_with_llm(orchestrator_llm, user_text, prompt)
        if not subtasks:
            subtasks = decompose_heuristic(user_text)
        # 空子任务 → 兜底一条业务子任务（finalize 直接处理）
        if not subtasks:
            subtasks = [{"kind": "business", "text": user_text or "(空任务)"}]
        return {
            "subtasks": subtasks,
            "_stream_messages": [
                _msg(StreamEvents.MULTI_AGENT_DECOMPOSE, {"task": user_text})
            ],
        }

    return _decompose


def dispatch(state: Dict[str, Any]) -> List[Send]:
    """条件边：按 kind 并行分发到 business_worker / command_worker 子图节点。"""
    sends: List[Send] = []
    for st in state.get("subtasks") or []:
        target = "command_worker" if st.get("kind") == "command" else "business_worker"
        sends.append(
            Send(target, {"messages": [HumanMessage(content=str(st.get("text", "")))]})
        )
    return sends


def gather_node_factory(loop: Any) -> Any:
    """gather 节点：从 messages 收集各 worker 的 AI 结果，产子任务明细事件。"""

    def _gather(state: Dict[str, Any]) -> Dict[str, Any]:
        ai_results = [
            str(m.content)
            for m in state.get("messages") or []
            if m.type == "ai" and m.content
        ]
        subtasks = state.get("subtasks") or []
        records = [
            {
                "index": i,
                "kind": st.get("kind", "business"),
                "text": st.get("text", ""),
                "result": ai_results[i] if i < len(ai_results) else "(无结果)",
            }
            for i, st in enumerate(subtasks)
        ]
        return {
            "_stream_messages": [
                _msg(StreamEvents.MULTI_AGENT_SUBAGENT, {"records": records})
            ]
        }

    return _gather


def finalize_node_factory(
    finalizer_llm: Any,
    *,
    system_prompt: str = MAIN_SYSTEM_PROMPT,
    knowledge_text: str = "",
) -> Any:
    """finalize 节点：终局归纳 LLM 生成最终答复（MULTI_AGENT_DONE/ANSWER_END）。"""

    def _finalize(state: Dict[str, Any]) -> Dict[str, Any]:
        messages: list[BaseMessage] = list(state.get("messages") or [])
        user_text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                user_text = str(m.content or "")
                break
        subtasks = state.get("subtasks") or []
        # 归并子任务结果（AI 消息）
        ai_results = [str(m.content) for m in messages if m.type == "ai" and m.content]
        merged_lines = ["共 %d 个子任务:" % len(subtasks), ""]
        for i, st in enumerate(subtasks):
            result = ai_results[i] if i < len(ai_results) else "(无结果)"
            merged_lines.append(f"  [{st.get('kind')}] {st.get('text')}")
            merged_lines.append(f"      → {result}")
        merged = "\n".join(merged_lines)

        prompt = system_prompt
        if knowledge_text:
            prompt += "\n\n" + knowledge_text
        final_text = merged
        try:
            response = finalizer_llm.invoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(content=f"原始任务: {user_text}\n\n编排结果:\n{merged}"),
                ]
            )
            content = (getattr(response, "content", "") or "").strip()
            if content:
                final_text = content
        except Exception as exc:  # noqa: BLE001 - 回退归并原文
            logger.warning("终局归纳失败，回退归并原文: %s", exc)

        return {
            "_stream_messages": [
                _msg(StreamEvents.MULTI_AGENT_DONE, {"content": final_text, "task": user_text}),
                _msg(StreamEvents.ANSWER_DELTA, {"content": final_text}),
                _msg(
                    StreamEvents.ANSWER_END,
                    {
                        "content": final_text,
                        "messages": [
                            *messages_to_dict(messages),
                            {"role": "assistant", "content": final_text},
                        ],
                    },
                ),
            ],
        }

    return _finalize


def build_multi_agent_graph(
    *,
    orchestrator_llm: Any,
    finalizer_llm: Any,
    business_worker: Any,
    command_worker: Any,
    checkpointer: Any = None,
    store: Any = None,
    knowledge_text: str = "",
    emit: Optional[Any] = None,
) -> Any:
    """组装并编译多 Agent 主编排图。

    参数:
        orchestrator_llm: 拆解 LLM（decompose 节点）。
        finalizer_llm: 终局归纳 LLM（finalize 节点）。
        business_worker: create_agent 业务 worker（官方子图）。
        command_worker: create_agent 命令 worker（官方子图，工具含审批）。
        checkpointer: 与 workers 共享的 checkpointer（SqliteSaver/InMemorySaver）。
        store: langgraph store（长期记忆）。
        knowledge_text: 环境知识（注入拆解/归纳提示）。
        emit: 事件发射器（AgentEventEnvelope 用；None 零开销）。
    """
    loop = type("_L", (), {"event_emitter": emit})() if emit is not None else None
    builder = StateGraph(MultiGraphState)
    builder.add_node("decompose", decompose_node_factory(orchestrator_llm, knowledge_text=knowledge_text))
    builder.add_node("business_worker", business_worker)  # 官方子图节点
    builder.add_node("command_worker", command_worker)  # 官方子图节点
    builder.add_node("gather", gather_node_factory(loop))
    builder.add_node("finalize", finalize_node_factory(finalizer_llm, knowledge_text=knowledge_text))

    builder.add_edge(START, "decompose")
    builder.add_conditional_edges("decompose", dispatch, ["business_worker", "command_worker"])
    builder.add_edge("business_worker", "gather")
    builder.add_edge("command_worker", "gather")
    builder.add_edge("gather", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer, store=store)


__all__ = [
    "MAIN_SYSTEM_PROMPT",
    "MultiGraphState",
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "build_multi_agent_graph",
    "decompose_heuristic",
    "dispatch",
    "plan_with_llm",
]
