"""长期记忆用户隔离单元测试（spec SC-001/SC-002/SC-004，FR-002/004）。"""

import sqlite3

import pytest

from GSagent.core.memory.store import MemoryStore
from GSagent.core.memory.user import resolve_user_key

SCOPE = "proj-x"


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(path=tmp_path / "memory.db", max_entries=10)


class TestUserIsolation:
    def test_remember_search_isolated(self, store):
        """两用户同 scope 各自 remember → search 互不可见（SC-001）。"""
        store.remember(scope=SCOPE, content="A 的偏好", user="alice")
        store.remember(scope=SCOPE, content="B 的偏好", user="bob")
        assert len(store.search(SCOPE, "偏好", user="alice")) == 1
        assert store.search(SCOPE, "偏好", user="alice")[0]["content"] == "A 的偏好"
        assert store.search(SCOPE, "偏好", user="bob")[0]["content"] == "B 的偏好"

    def test_list_isolated(self, store):
        store.remember(scope=SCOPE, content="x1", user="alice")
        store.remember(scope=SCOPE, content="x2", user="bob")
        assert [r["content"] for r in store.list(SCOPE, user="alice")] == ["x1"]
        assert [r["content"] for r in store.list(SCOPE, user="bob")] == ["x2"]

    def test_same_content_not_shared_dedup(self, store):
        """相同内容不跨用户去重（US1 验收 3：各自成条）。"""
        a = store.remember(scope=SCOPE, content="共同事实", user="alice")
        b = store.remember(scope=SCOPE, content="共同事实", user="bob")
        assert a["id"] != b["id"]
        assert a["access_count"] == 0 and b["access_count"] == 0

    def test_same_content_strengthens_same_user(self, store):
        """同用户重复 remember 仍去重强化。"""
        r1 = store.remember(scope=SCOPE, content="事实", user="alice")
        r2 = store.remember(scope=SCOPE, content="事实", user="alice")
        assert r1["id"] == r2["id"]
        assert r2["access_count"] == 1

    def test_stats_isolated(self, store):
        store.remember(scope=SCOPE, content="a", user="alice")
        store.remember(scope=SCOPE, content="a", user="bob")
        store.remember(scope=SCOPE, content="b", user="bob")
        assert store.stats(SCOPE, user="alice")["total"] == 1
        assert store.stats(SCOPE, user="bob")["total"] == 2


class TestCleanupIsolation:
    def test_forget_scope_does_not_affect_other_user(self, store):
        """A forget_scope 不影响 B（SC-002）。"""
        store.remember(scope=SCOPE, content="a1", user="alice")
        store.remember(scope=SCOPE, content="b1", user="bob")
        assert store.forget_scope(SCOPE, user="alice") == 1
        assert [r["content"] for r in store.list(SCOPE, user="bob")] == ["b1"]

    def test_forget_cross_user_returns_false(self, store):
        """A 不能 forget B 的记忆（跨 user 返回 False）。"""
        rec = store.remember(scope=SCOPE, content="b1", user="bob")
        assert store.forget(SCOPE, rec["id"], user="alice") is False
        assert len(store.list(SCOPE, user="bob")) == 1

    def test_prune_isolated(self, store):
        """A 触发 LRU 淘汰不影响 B（SC-002）。"""
        for i in range(10):
            store.remember(scope=SCOPE, content=f"a{i}", user="alice")
        store.remember(scope=SCOPE, content="b-keep", user="bob")
        # alice 超 max_entries=10 → 淘汰；bob 应不受影响
        assert len(store.list(SCOPE, user="bob")) == 1
        assert len(store.list(SCOPE, user="alice")) == 10  # 恰好 max_entries


class TestSchemaMigration:
    def test_legacy_db_migrates_to_default_user(self, tmp_path):
        """旧库（无 user 列）迁移：数据归属默认用户可读（SC-004 零迁移）。"""
        db = tmp_path / "legacy.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                content_hash TEXT NOT NULL, source TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_access_at TEXT NOT NULL, access_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE (scope, content_hash)
            );
            INSERT INTO memories (scope, kind, content, content_hash, source,
                created_at, updated_at, last_access_at, access_count)
            VALUES ('legacy-scope', 'fact', '旧数据', 'h1', 'manual',
                '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z',
                '2026-01-01T00:00:00.000Z', 0);
            """
        )
        con.close()

        store = MemoryStore(path=db)  # 构造触发 _ensure_schema + 迁移
        default_user = resolve_user_key()
        rows = store.list("legacy-scope", user=default_user)
        assert len(rows) == 1
        assert rows[0]["content"] == "旧数据"
        assert rows[0]["user"] == default_user

    def test_migration_idempotent(self, tmp_path):
        """已有 user 列的库不重复迁移（幂等）。"""
        store = MemoryStore(path=tmp_path / "m.db")
        store.remember(scope=SCOPE, content="x", user="alice")
        # 再次构造同一路径 → 不迁移、数据保留
        store2 = MemoryStore(path=store.path)
        assert len(store2.list(SCOPE, user="alice")) == 1


class TestDefaultUserBackCompat:
    def test_no_user_param_defaults_to_system(self, store):
        """不传 user → 默认系统用户，行为不变（SC-004）。"""
        store.remember(scope=SCOPE, content="默认用户记忆")
        rows = store.list(SCOPE)  # 不传 user
        assert len(rows) == 1
        assert rows[0]["user"] == resolve_user_key()
