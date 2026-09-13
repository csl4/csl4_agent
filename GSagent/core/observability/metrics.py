"""指标聚合：从事件流生成 Agent/Task 指标快照（唯一写入口）。

宪法对象模型规范：AgentMetrics/TaskMetrics 为聚合快照，原始数据来源只能是
AgentEventEnvelope 事件流。本模块是**唯一**聚合路径——指标模型 frozen、无 setter，
不存在绕过事件流的手工修改入口（spec FR-003 / SC-003）。
"""

from typing import Dict, List, Tuple

from GSagent.core.observability.models import (
    AgentEventEnvelope,
    AgentEventType,
    AgentMetrics,
    TaskMetrics,
)


class MetricsAggregator:
    """事件流 → 指标快照聚合器（纯函数，确定性）。"""

    @staticmethod
    def _error_types() -> set:
        """事件类型中计入 error_count 的集合（*_ERROR）。"""
        return {
            AgentEventType.LLM_ERROR,
            AgentEventType.TOOL_ERROR,
        }

    @classmethod
    def aggregate(
        cls, events: List[AgentEventEnvelope]
    ) -> Tuple[Dict[str, AgentMetrics], TaskMetrics]:
        """聚合事件流为 ``{agent_id: AgentMetrics}`` 与 ``TaskMetrics``。

        - 按 ``agent_id`` 分组统计（LLM/工具调用数、token/cost、错误数、最新时间戳）
        - ``TaskMetrics`` 汇总本任务全部事件的 token/cost/错误
        - 空 ``agent_id`` 事件不形成 Agent 指标，但计入 Task 汇总
        """
        # 按 agent 累积（可变中间态，最后构造 frozen 快照）
        acc: Dict[str, dict] = {}
        task_acc = {
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_cost_usd": 0.0,
            "total_error_count": 0,
        }
        error_types = cls._error_types()

        for ev in events:
            # Task 汇总（全部事件）
            task_acc["total_tokens_in"] += ev.tokens_in
            task_acc["total_tokens_out"] += ev.tokens_out
            task_acc["total_cost_usd"] += ev.cost_usd
            if ev.event_type in error_types:
                task_acc["total_error_count"] += 1

            # Agent 指标（跳过无 agent_id 的事件）
            if not ev.agent_id:
                continue
            bucket = acc.setdefault(
                ev.agent_id,
                {
                    "total_tokens_in": 0,
                    "total_tokens_out": 0,
                    "total_cost_usd": 0.0,
                    "llm_call_count": 0,
                    "tool_call_count": 0,
                    "error_count": 0,
                    "last_event_timestamp": None,
                },
            )
            bucket["total_tokens_in"] += ev.tokens_in
            bucket["total_tokens_out"] += ev.tokens_out
            bucket["total_cost_usd"] += ev.cost_usd
            if ev.event_type == AgentEventType.LLM_REQUEST:
                bucket["llm_call_count"] += 1
            elif ev.event_type == AgentEventType.TOOL_CALL_START:
                bucket["tool_call_count"] += 1
            if ev.event_type in error_types:
                bucket["error_count"] += 1
            if ev.timestamp:
                # 时间升序聚合时，后到的覆盖为最新时间戳
                bucket["last_event_timestamp"] = ev.timestamp

        agent_metrics = {
            aid: AgentMetrics(**vals) for aid, vals in acc.items()
        }
        task_metrics = TaskMetrics(**task_acc)
        return agent_metrics, task_metrics

    @classmethod
    def aggregate_session(
        cls, events: List[AgentEventEnvelope]
    ) -> Tuple[Dict[str, AgentMetrics], TaskMetrics]:
        """会话级跨任务聚合：输入会话全部事件，得到各 Agent 累计指标（spec US2）。

        与 ``aggregate`` 同一实现；语义上明确输入为会话维度事件全集。
        """
        return cls.aggregate(events)
