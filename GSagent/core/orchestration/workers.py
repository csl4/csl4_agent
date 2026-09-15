"""create_agent worker 工厂（纯 langgraph 重构，Phase 3）。

多 Agent 四角色中的 worker（业务/命令）用官方 ``langchain.agents.create_agent``
（运行在 LangGraph 之上）。checkpointer / store 与主编排图**共享**（组合根构造
一次传入），保证审批恢复与长期记忆跨层一致。

- ``create_business_worker``：纯文本业务/分析子 Agent（无工具）。
- ``create_command_worker``：命令执行子 Agent（全工具，经 ``wrap_with_approval``
  审批下沉——工具在 create_agent 内部 ToolNode 执行时触发 interrupt）。
"""

from typing import Any, Optional

from langchain.agents import create_agent

from GSagent.core.tools.approval import wrap_all_with_approval
from GSagent.core.tools.registry import ToolRegistry

BUSINESS_SYSTEM_PROMPT = (
    "你是多Agent 系统中的业务/分析子 Agent。针对用户给出的领域问题，"
    "给出清晰、准确、结构化的回答。不要编造事实。"
)

COMMAND_SYSTEM_PROMPT = (
    "你是多Agent 系统中的命令执行子 Agent。使用可用工具（bash/sandbox 等）"
    "完成用户请求。需要执行命令时直接调用工具并报告执行结果；命令失败要如实"
    "说明原因，不要假装成功。"
)


def create_business_worker(
    chat_model: Any,
    *,
    checkpointer: Optional[Any] = None,
    store: Optional[Any] = None,
    system_prompt: str = BUSINESS_SYSTEM_PROMPT,
) -> Any:
    """创建业务 worker（create_agent，无工具）。"""
    return create_agent(
        chat_model,
        tools=[],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
    )


def create_command_worker(
    chat_model: Any,
    *,
    tools_registry: ToolRegistry,
    checkpointer: Optional[Any] = None,
    store: Optional[Any] = None,
    system_prompt: str = COMMAND_SYSTEM_PROMPT,
) -> Any:
    """创建命令 worker（create_agent，全工具 + 审批下沉包装）。"""
    tools = wrap_all_with_approval(
        tools_registry.get_all_tools(), registry=tools_registry
    )
    return create_agent(
        chat_model,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
    )


__all__ = [
    "BUSINESS_SYSTEM_PROMPT",
    "COMMAND_SYSTEM_PROMPT",
    "create_business_worker",
    "create_command_worker",
]
