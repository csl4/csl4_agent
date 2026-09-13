"""企业级审计日志：不可变 JSONL 追加 + 敏感字段脱敏（FR-005/006）。

契约见 specs/002-enterprise-cli-upgrade/contracts/audit.md（R-05）。
与 history（会话可读记录）职责分离：audit 是合规不可变留痕。

设计：
- 每行一个 JSON 事件：{ts, session_id, type, payload, outcome, approver, cwd, usage}
- 只追加、不修改历史（SC-003 不可变）
- payload 落库前经 redact() 递归脱敏——键名含敏感子串的值替换为 "***"
  （0 明文，SC-003）；usage 作为独立字段不透红（token 计数非敏感）
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from GSagent.common import DEFAULT_AUDIT_DIR

# 敏感键名子串（大小写不敏感），对齐 PaiCLI SENSITIVE_KEYS 语义（R-05）。
SENSITIVE_KEYS: Tuple[str, ...] = (
    "token",
    "key",
    "password",
    "secret",
    "authorization",
    "bearer",
    "api_key",
)

REDACTED = "***"

# DEFAULT_AUDIT_DIR 由 GSagent.common 统一提供（./.GSagent/audit）。


def _is_sensitive_key(key: str) -> bool:
    """键名是否含敏感子串（大小写不敏感）。"""
    lower = key.lower()
    return any(s in lower for s in SENSITIVE_KEYS)


def redact(value: Any) -> Any:
    """递归脱敏：键名含敏感子串的值替换为 '***'；其余原样递归。"""
    if isinstance(value, dict):
        return {
            str(k): (REDACTED if _is_sensitive_key(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _utc_now_iso() -> str:
    """UTC 时间戳 ISO 字符串（契约用 Z 后缀）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AuditLog:
    """不可变追加式审计日志（JSONL）。

    record() 追加事件（自动脱敏 payload 并建目录），tail() 从最新往前查询可过滤。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_AUDIT_DIR / "audit.jsonl"

    def record(
        self,
        *,
        event_type: str,
        payload: Dict[str, Any],
        outcome: str = "ok",
        session_id: str = "",
        approver: str = "",
        cwd: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        ts: Optional[str] = None,
    ) -> str:
        """追加一条审计事件，返回其时间戳。

        参数:
            event_type: model_call / tool_call / approval / error（contracts/audit.md）
            payload: 事件正文（落库前脱敏）
            outcome: ok / failed / timeout / blocked
            approver: user / none / ""（approval 相关事件；none=拒绝，FR-004）
            usage: model_call 附带用量/成本（FR-007，不透红）
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event: Dict[str, Any] = {
            "ts": ts or _utc_now_iso(),
            "session_id": session_id,
            "type": event_type,
            "payload": redact(payload),
            "outcome": outcome,
            "approver": approver,
            "cwd": cwd or os.getcwd(),
        }
        if usage is not None:
            event["usage"] = usage
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event["ts"]

    def tail(
        self,
        limit: int = 50,
        *,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """从最新往前返回至多 N 条事件（线性扫描，可按会话/类型/结果过滤）。

        文件不存在返回空列表；损坏行跳过（不中断追溯）。
        """
        if not self.path.exists():
            return []
        events: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if session_id is not None and event.get("session_id") != session_id:
                    continue
                if event_type is not None and event.get("type") != event_type:
                    continue
                if outcome is not None and event.get("outcome") != outcome:
                    continue
                events.append(event)
        return events[-limit:]


__all__ = [
    "AuditLog",
    "DEFAULT_AUDIT_DIR",
    "REDACTED",
    "SENSITIVE_KEYS",
    "redact",
]
