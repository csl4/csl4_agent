"""企业级审计/用量埋点 mixin（ToolCallingLLM 共享）。

从 `tool_calling_llm.py` 抽出的横向切面：模型调用审计、审批决策留痕、
错误事件、用量/成本折算（FR-005/007，contracts/audit.md）。

依赖宿主提供三个属性（构造时注入）：
- ``self.audit_log``（AuditLog | None）——审计日志，None 即零开销
- ``self.cost_estimator``（CostEstimator | None）——本地定价成本估算
- ``self.record_usage``（bool）——是否记录用量/成本
"""

from typing import Any, Dict, Optional

from GSagent.core.observability import CostEstimator
from GSagent.core.providers import ModelResponse


class AuditUsageMixin:
    """审计 + 用量/成本埋点方法集（无状态，属性来自宿主类）。"""

    @staticmethod
    def _session_id(request_context: Optional[Dict[str, Any]]) -> str:
        """从 request_context 提取 session_id（缺省空串）。"""
        if not request_context:
            return ""
        return str(request_context.get("session_id", "") or "")

    def _usage_with_cost(self, response: ModelResponse) -> Dict[str, Any]:
        """把 usage 明细补上 model 与估算成本（ANSWER_END/USAGE/审计共用）。

        始终输出契约的完整键集（contracts/audit.md）：即便供应商未回填 usage，
        prompt/completion/cache/reasoning 也以 0 占位，保证审计形态稳定。
        """
        usage = response.usage.model_dump() if response.usage else {}
        cost = 0.0
        if self.record_usage and self.cost_estimator is not None:
            cost = self.cost_estimator.estimate(
                response.model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
        return {
            "total_tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cache_read": usage.get("cache_read", 0),
            "cache_write": usage.get("cache_write", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
            "model": response.model,
            "estimated_cost": round(cost, 6),
        }

    def _audit_model_call(
        self, response: ModelResponse, request_context: Optional[Dict[str, Any]]
    ) -> None:
        """每次 LLM 调用一条 model_call 审计（含 usage/成本，FR-005/007）。"""
        if self.audit_log is None:
            return
        self.audit_log.record(
            event_type="model_call",
            payload={"model": response.model},
            session_id=self._session_id(request_context),
            usage=self._usage_with_cost(response),
        )

    def _audit_approval_user(
        self,
        tool_name: str,
        params: Dict[str, Any],
        conclusion: str,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """用户显式审批决策一条（approved/denied，approver=user，FR-004/005）。"""
        if self.audit_log is None:
            return
        self.audit_log.record(
            event_type="approval",
            payload={"tool": tool_name, "params": params, "conclusion": conclusion},
            outcome="ok" if conclusion == "approved" else "blocked",
            approver="user",
            session_id=self._session_id(request_context),
        )

    def _audit_approval_blocked(self, tool_name: str, params: Dict[str, Any]) -> None:
        """审批拒绝（approver=none，FR-004）写 `blocked` 审计事件（T016）。"""
        if self.audit_log is None:
            return
        self.audit_log.record(
            event_type="approval",
            payload={
                "tool": tool_name,
                "reason": "hitl_approver_none",
                "params": params,
            },
            outcome="blocked",
            approver="none",
        )

    def _audit_error(
        self,
        tool_name: str,
        message: str,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """系统级错误（工具未找到/参数损坏/超步数）一条 error 事件。"""
        if self.audit_log is None:
            return
        self.audit_log.record(
            event_type="error",
            payload={"tool": tool_name or "", "error": message},
            outcome="failed",
            session_id=self._session_id(request_context),
        )
