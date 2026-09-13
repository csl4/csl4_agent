"""企业级安全策略层（002-enterprise-cli-upgrade）。

统一收拢审批/守卫/审计的横向切面（R-01）：
- path_guard.py    工作区路径守卫（FR-001）
- command_guard.py 破坏性命令守卫（FR-002）
- hitl.py          审批模式三态 + approval_callback（FR-003/004）
- audit.py         不可变审计 JSONL + 敏感脱敏（FR-005/006）
- rules.py         工具分类规则（哪些工具由哪个守卫校验，T013）
"""

from GSagent.core.policy.audit import AuditLog
from GSagent.core.policy.command_guard import CommandGuard
from GSagent.core.policy.hitl import HitlPolicy, VALID_MODES
from GSagent.core.policy.input_guard import GuardResult, InputGuard
from GSagent.core.policy.output_guard import OutputCheckResult, OutputGuard
from GSagent.core.policy.path_guard import PathGuard
from GSagent.core.policy.rules import guard_kind_for

__all__ = [
    "AuditLog",
    "CommandGuard",
    "GuardResult",
    "HitlPolicy",
    "InputGuard",
    "OutputCheckResult",
    "OutputGuard",
    "PathGuard",
    "VALID_MODES",
    "guard_kind_for",
]

