"""长期记忆的 langgraph store 适配器（纯 langgraph 重构，Phase 2）。

把既有 ``MemoryStore``（自研 SQLite 手工建表）的业务语义迁移到 langgraph
``BaseStore``（InMemoryStore / SqliteStore）。业务语义层保留：
- namespace 设计：``(user_key, scope)`` —— 天然 per-user/per-scope 隔离。
- key = ``content_hash``（sha256）—— 去重 upsert，重复 remember 视为强化。
- value = 记录 dict（kind/content/source/时间戳/access_count）。
- 离线召回：search 拉取全量后复用 ``_tokenize`` 打分公式（命中词条数×100 +
  access 加权 + recency 加权）重排 —— store 无 embedding 时不做语义检索。
- 配额清理：prune 按 ``last_access_at`` LRU 淘汰到 ``max_entries``。

对外 API 与 ``MemoryStore`` 完全一致（remember/list/search/stats/forget/
forget_scope/prune），CLI 与 memory 工具集可无缝切换。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from langgraph.store.base import BaseStore

from GSagent.core.memory.store import MEMORY_KINDS, _content_hash, _iso, _tokenize
from GSagent.core.memory.user import resolve_user_key

logger = logging.getLogger(__name__)


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.rstrip("Z") + "+00:00")
    except (ValueError, TypeError):
        return None


class StoreMemoryAdapter:
    """长期记忆业务语义适配器（over langgraph BaseStore）。

    与 ``MemoryStore`` 同 API；记录 dict 含 ``id``（= content_hash，供 forget）。
    """

    def __init__(
        self,
        store: BaseStore,
        *,
        max_entries: int = 500,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._store = store
        self.max_entries = max_entries
        self._clock = clock or _default_clock

    # ---- 内部 ----
    @staticmethod
    def _item_to_dict(item: Any, user: str, scope: str) -> Dict[str, Any]:
        v = dict(item.value or {})
        h = v.get("content_hash") or item.key
        return {
            "id": h,
            "user": user,
            "scope": scope,
            "content_hash": h,
            **v,
        }

    def _all_items(self, scope: str, user: str) -> List[Any]:
        """拉取 user+scope namespace 下全部 item（无 embedding 的本地召回基础）。"""
        return list(self._store.search((user, scope), limit=100000))

    # ---- 写入 ----
    def remember(
        self,
        scope: str,
        content: str,
        kind: str = "fact",
        source: str = "manual",
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """写入/强化一条长期记忆（content_hash 去重 upsert）。"""
        content = str(content or "").strip()
        if not content:
            raise ValueError("content 不能为空")
        if kind not in MEMORY_KINDS:
            raise ValueError(f"kind 必须是 {MEMORY_KINDS} 之一，收到 {kind!r}")

        user_key = user or resolve_user_key()
        ns = (user_key, scope)
        now = _iso(self._clock())
        h = _content_hash(content)

        existing = self._store.get(ns, h)
        if existing is not None:
            v = dict(existing.value)
            v["kind"] = kind
            v["source"] = source
            v["updated_at"] = now
            v["last_access_at"] = now
            v["access_count"] = int(v.get("access_count", 0)) + 1
            self._store.put(ns, key=h, value=v)
            rec = {**v, "user": user_key, "scope": scope, "content_hash": h, "id": h}
        else:
            v = {
                "kind": kind,
                "content": content,
                "source": source,
                "content_hash": h,
                "created_at": now,
                "updated_at": now,
                "last_access_at": now,
                "access_count": 0,
            }
            self._store.put(ns, key=h, value=v)
            rec = {**v, "user": user_key, "scope": scope, "id": h}

        self.prune(scope, user=user_key)
        return rec

    # ---- 查询 ----
    def list(
        self,
        scope: str,
        kind: Optional[str] = None,
        limit: int = 50,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出 user+scope 下最近更新的记忆（kind 可选过滤，最新在前）。"""
        user_key = user or resolve_user_key()
        rows = [
            self._item_to_dict(it, user_key, scope)
            for it in self._all_items(scope, user_key)
        ]
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        rows.sort(key=lambda r: (r.get("updated_at") or "", r.get("id") or ""), reverse=True)
        return rows[:limit]

    def search(
        self,
        scope: str,
        query: str,
        limit: int = 10,
        kinds: Optional[List[str]] = None,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """离线关键词召回：词条重叠打分 + access/recency 加权，相关度降序。"""
        terms = _tokenize(query)
        if not terms:
            return self.list(scope, limit=limit, user=user)

        user_key = user or resolve_user_key()
        rows = [
            self._item_to_dict(it, user_key, scope)
            for it in self._all_items(scope, user_key)
        ]
        now = self._clock()
        scored: List[tuple] = []
        for row in rows:
            content = str(row.get("content", "")).lower()
            matched = [t for t in terms if t in content]
            if not matched:
                continue
            if kinds and row.get("kind") not in kinds:
                continue
            score = len(matched) * 100 + min(int(row.get("access_count", 0)), 10) * 10
            updated = _parse_iso(str(row.get("updated_at", "")))
            if updated is not None:
                age_days = max(0.0, (now - updated).total_seconds() / 86400.0)
                score += max(0, 50 - int(age_days * 5))
            scored.append((score, row.get("updated_at") or "", row))
        scored.sort(key=lambda item: (-item[0], item[1]), reverse=False)
        return [r for _, _, r in scored[:limit]]

    def stats(self, scope: str, user: Optional[str] = None) -> Dict[str, Any]:
        """user+scope 下记忆统计：总数 + 按 kind 计数。"""
        user_key = user or resolve_user_key()
        rows = [
            self._item_to_dict(it, user_key, scope)
            for it in self._all_items(scope, user_key)
        ]
        by_kind: Dict[str, int] = {}
        for r in rows:
            k = r.get("kind", "fact")
            by_kind[k] = by_kind.get(k, 0) + 1
        return {"total": len(rows), "by_kind": by_kind}

    # ---- 删除 ----
    def forget(self, scope: str, memory_id: str, user: Optional[str] = None) -> bool:
        """按 id（content_hash）删除；不存在 → False。"""
        user_key = user or resolve_user_key()
        existing = self._store.get((user_key, scope), memory_id)
        if existing is None:
            return False
        self._store.delete((user_key, scope), memory_id)
        return True

    def forget_scope(self, scope: str, user: Optional[str] = None) -> int:
        """清空 user+scope 下所有记忆，返回删除条数。"""
        user_key = user or resolve_user_key()
        items = self._all_items(scope, user_key)
        for it in items:
            self._store.delete((user_key, scope), it.key)
        return len(items)

    def prune(self, scope: str, user: Optional[str] = None) -> int:
        """按 last_access_at LRU 淘汰 user+scope 下超出 max_entries 的最旧记忆。"""
        if self.max_entries <= 0:
            return 0
        user_key = user or resolve_user_key()
        items = self._all_items(scope, user_key)
        if len(items) <= self.max_entries:
            return 0
        # 最旧在前（升序），超出 max_entries 的最旧部分删除
        ordered = sorted(
            items,
            key=lambda it: (
                str(it.value.get("last_access_at", "")) if isinstance(it.value, dict) else "",
                str(it.key),
            ),
            reverse=True,  # 最新在前
        )
        to_drop = ordered[self.max_entries :]
        for it in to_drop:
            self._store.delete((user_key, scope), it.key)
        return len(to_drop)

    def close(self) -> None:
        """无持有连接可关（BaseStore 自管理连接）；保留兼容语义。"""
        return None


__all__ = ["StoreMemoryAdapter"]
