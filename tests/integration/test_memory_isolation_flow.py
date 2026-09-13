"""记忆隔离端到端集成测试（spec US1/US2 验收，SC-001~SC-005，quickstart V1-V5）。"""

from GSagent.core.memory import MemoryStore, SessionMemoryStore
from GSagent.core.models import ToolInvokeContext
from GSagent.plugins.toolsets.memory.toolset import create_memory_toolset

SCOPE = "proj-x"


class TestLongTermMemoryEndToEnd:
    """US1 长期记忆多用户隔离端到端（SC-001/002/005）。"""

    def test_two_user_full_flow(self, tmp_path):
        """A/B remember → search 互不可见 → A forget_scope 不影响 B（SC-001/002）。"""
        db = tmp_path / "memory.db"
        store_a = MemoryStore(path=db)
        store_b = MemoryStore(path=db)

        store_a.remember(scope=SCOPE, content="A 的决策：用 uv 管理依赖", user="alice")
        store_b.remember(scope=SCOPE, content="B 的约束：部署到内网", user="bob")

        # 互不可见（SC-001）
        assert store_a.search(SCOPE, "依赖", user="alice")[0]["content"].startswith("A 的决策")
        assert store_a.search(SCOPE, "部署", user="alice") == []  # 看不到 B 的
        assert store_b.search(SCOPE, "部署", user="bob")[0]["content"].startswith("B 的约束")

        # A 清空不影响 B（SC-002）
        assert store_a.forget_scope(SCOPE, user="alice") == 1
        assert len(store_b.list(SCOPE, user="bob")) == 1

    def test_tool_and_store_isolation_consistent(self, tmp_path):
        """工具 remember/search 与底层 store 隔离一致（SC-005）。"""
        db = str(tmp_path / "shared.db")
        ts_a = create_memory_toolset({"db_path": db, "user": "alice", "scope": SCOPE})
        ts_b = create_memory_toolset({"db_path": db, "user": "bob", "scope": SCOPE})

        ts_a.tools[0]._invoke({"content": "工具记忆 A"}, ToolInvokeContext(toolset=ts_a))
        ts_b.tools[0]._invoke({"content": "工具记忆 B"}, ToolInvokeContext(toolset=ts_b))

        # 经底层 store 直接查询也应隔离（工具视角与存储视角一致）
        store = MemoryStore(path=db)
        assert len(store.search(SCOPE, "工具记忆", user="alice")) == 1
        assert len(store.search(SCOPE, "工具记忆", user="bob")) == 1


class TestSessionMemoryEndToEnd:
    """US2 会话记忆多用户隔离端到端（SC-003）。"""

    def test_two_user_same_session_id(self, tmp_path):
        """两用户保存相同 session_id → 目录隔离、各自可恢复。"""
        root = tmp_path / "sessions"
        sa = SessionMemoryStore(root=root, user="alice")
        sb = SessionMemoryStore(root=root, user="bob")

        hist_a = [{"role": "user", "content": "A 的对话"}]
        hist_b = [{"role": "user", "content": "B 的对话"}]
        assert sa.save("cli-shared", hist_a)
        assert sb.save("cli-shared", hist_b)

        _, got_a = sa.load("cli-shared")
        _, got_b = sb.load("cli-shared")
        assert got_a[0]["content"] == "A 的对话"
        assert got_b[0]["content"] == "B 的对话"

        # list 互不可见
        assert [m["session_id"] for m in sa.list_sessions()] == ["cli-shared"]
        assert [m["session_id"] for m in sb.list_sessions()] == ["cli-shared"]


class TestBackwardCompat:
    """SC-004 默认用户零迁移。"""

    def test_default_user_behavior_unchanged(self, tmp_path):
        """不传 user 的既有调用 → 默认系统用户，行为不变。"""
        store = MemoryStore(path=tmp_path / "m.db")
        store.remember(scope=SCOPE, content="既有调用记忆")
        assert len(store.list(SCOPE)) == 1  # 不传 user
