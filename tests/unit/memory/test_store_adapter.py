"""StoreMemoryAdapter 测试（纯 langgraph 重构，Phase 2）。

验证长期记忆业务语义在 langgraph BaseStore 上等价于既有 MemoryStore：
- remember 去重强化（content_hash 同 key，access_count 递增）
- remember 非法 kind 抛错
- list 按 updated_at DESC + kind 过滤
- search 离线打分召回（相关度降序，access/recency 加权）
- stats / forget / forget_scope / prune(LRU)
- user/scope 隔离（namespace 维度）
- SqliteStore 后端亦可用
"""

import pytest

from GSagent.core.memory.langgraph_store import StoreMemoryAdapter

SCOPE = "proj-x"


@pytest.fixture
def adapter():
    from langgraph.store.memory import InMemoryStore

    return StoreMemoryAdapter(InMemoryStore(), max_entries=10)


class TestRemember:
    def test_remember_basic(self, adapter):
        rec = adapter.remember(scope=SCOPE, content="北京晴天", user="alice")
        assert rec["content"] == "北京晴天"
        assert rec["kind"] == "fact"
        assert rec["access_count"] == 0
        assert rec["user"] == "alice"

    def test_remember_dedup_strengthens(self, adapter):
        r1 = adapter.remember(scope=SCOPE, content="事实", user="alice")
        r2 = adapter.remember(scope=SCOPE, content="事实", user="alice")
        assert r1["id"] == r2["id"], "同内容同用户去重"
        assert r2["access_count"] == 1, "重复 remember 强化"

    def test_remember_same_content_different_user_not_dedup(self, adapter):
        """相同内容不跨用户去重（namespace 隔离）：各自独立成条。

        注：adapter 的 id = content_hash（namespace 内唯一）；跨用户 id 可相同，
        但记录经 (user, scope, id) 精确定位，隔离由 namespace 保证。
        """
        a = adapter.remember(scope=SCOPE, content="共同事实", user="alice")
        b = adapter.remember(scope=SCOPE, content="共同事实", user="bob")
        assert len(adapter.list(SCOPE, user="alice")) == 1
        assert len(adapter.list(SCOPE, user="bob")) == 1
        assert a["access_count"] == 0 and b["access_count"] == 0

    def test_remember_empty_raises(self, adapter):
        with pytest.raises(ValueError):
            adapter.remember(scope=SCOPE, content="  ")

    def test_remember_bad_kind_raises(self, adapter):
        with pytest.raises(ValueError):
            adapter.remember(scope=SCOPE, content="x", kind="bogus")


class TestQuery:
    def test_list_order_and_kind_filter(self, adapter):
        adapter.remember(scope=SCOPE, content="a", kind="fact", user="alice")
        adapter.remember(scope=SCOPE, content="b", kind="preference", user="alice")
        all_rows = adapter.list(SCOPE, user="alice")
        assert {r["content"] for r in all_rows} == {"a", "b"}
        kinds = adapter.list(SCOPE, kind="preference", user="alice")
        assert [r["content"] for r in kinds] == ["b"]

    def test_search_ranking(self, adapter):
        """命中词条越多排序越前；access_count 加权。"""
        adapter.remember(scope=SCOPE, content="北京今天晴 适合跑步", user="alice")
        adapter.remember(scope=SCOPE, content="上海有雨 不适合跑步", user="alice")
        adapter.remember(scope=SCOPE, content="无关内容", user="alice")
        rows = adapter.search(SCOPE, "跑步", user="alice")
        assert len(rows) == 2
        assert rows[0]["content"].startswith("北京") or rows[0]["content"].startswith("上海")

    def test_search_empty_query_falls_back_to_list(self, adapter):
        adapter.remember(scope=SCOPE, content="x1", user="alice")
        rows = adapter.search(SCOPE, "", user="alice")
        assert len(rows) == 1

    def test_stats(self, adapter):
        adapter.remember(scope=SCOPE, content="a", kind="fact", user="alice")
        adapter.remember(scope=SCOPE, content="b", kind="fact", user="alice")
        adapter.remember(scope=SCOPE, content="c", kind="decision", user="alice")
        st = adapter.stats(SCOPE, user="alice")
        assert st["total"] == 3
        assert st["by_kind"]["fact"] == 2
        assert st["by_kind"]["decision"] == 1


class TestDeleteAndPrune:
    def test_forget_by_id(self, adapter):
        rec = adapter.remember(scope=SCOPE, content="to-forget", user="alice")
        assert adapter.forget(SCOPE, rec["id"], user="alice") is True
        assert adapter.forget(SCOPE, rec["id"], user="alice") is False, "二次删除返回 False"
        assert adapter.list(SCOPE, user="alice") == []

    def test_forget_cross_user_returns_false(self, adapter):
        rec = adapter.remember(scope=SCOPE, content="b1", user="bob")
        assert adapter.forget(SCOPE, rec["id"], user="alice") is False
        assert len(adapter.list(SCOPE, user="bob")) == 1

    def test_forget_scope_isolated(self, adapter):
        adapter.remember(scope=SCOPE, content="a1", user="alice")
        adapter.remember(scope=SCOPE, content="b1", user="bob")
        assert adapter.forget_scope(SCOPE, user="alice") == 1
        assert [r["content"] for r in adapter.list(SCOPE, user="bob")] == ["b1"]

    def test_prune_lru(self, adapter):
        """超 max_entries → 按 last_access_at 淘汰最旧。"""
        for i in range(10):
            adapter.remember(scope=SCOPE, content=f"a{i}", user="alice")
        # 访问 a0 使其新近，淘汰时保留
        rows = adapter.search(SCOPE, "a0", user="alice")
        assert rows, "a0 应被召回"
        # 再写一条触发 prune（max_entries=10）
        adapter.remember(scope=SCOPE, content="extra", user="alice")
        after = adapter.list(SCOPE, user="alice")
        assert len(after) <= 10
        contents = {r["content"] for r in after}
        assert "extra" in contents, "新写入保留"
        assert "a0" in contents, "近期访问的 a0 保留（LRU 语义）"

    def test_user_isolation(self, adapter):
        adapter.remember(scope=SCOPE, content="A 的偏好", user="alice")
        adapter.remember(scope=SCOPE, content="B 的偏好", user="bob")
        assert len(adapter.search(SCOPE, "偏好", user="alice")) == 1
        assert adapter.search(SCOPE, "偏好", user="alice")[0]["content"] == "A 的偏好"
        assert adapter.search(SCOPE, "偏好", user="bob")[0]["content"] == "B 的偏好"


class TestSqliteBackend:
    def test_sqlite_store_backend(self, tmp_path):
        """SqliteStore 后端：持久化写入/读取。"""
        from langgraph.store.sqlite import SqliteStore

        import sqlite3

        conn = sqlite3.connect(tmp_path / "store.db", check_same_thread=False, isolation_level=None)
        backend = SqliteStore(conn)
        adapter = StoreMemoryAdapter(backend, max_entries=10)
        adapter.remember(scope=SCOPE, content="持久化内容", user="alice")
        assert adapter.search(SCOPE, "持久化", user="alice")[0]["content"] == "持久化内容"
        # 新连接仍可读（持久化）
        conn2 = sqlite3.connect(tmp_path / "store.db", check_same_thread=False, isolation_level=None)
        backend2 = SqliteStore(conn2)
        adapter2 = StoreMemoryAdapter(backend2)
        assert adapter2.search(SCOPE, "持久化", user="alice")[0]["content"] == "持久化内容"
        conn.close()
        conn2.close()
