"""多Agent 角色实现：主 / 编排 / 业务 / 动态 SubAgent。"""

from agent.core.agents.base_agent import (
    AgentRole,
    BaseAgent,
    FAILED_STATES,
    run_task_safe,
    task_input_text,
    task_result_text,
    task_state_text,
)
from agent.core.agents.business_agent import BusinessAgent
from agent.core.agents.main_agent import MainAgent
from agent.core.agents.orchestrator import Orchestrator, merge_results
from agent.core.agents.subagent import SubAgent

__all__ = [
    "AgentRole",
    "BaseAgent",
    "BusinessAgent",
    "FAILED_STATES",
    "MainAgent",
    "Orchestrator",
    "SubAgent",
    "merge_results",
    "run_task_safe",
    "task_input_text",
    "task_result_text",
    "task_state_text",
]
