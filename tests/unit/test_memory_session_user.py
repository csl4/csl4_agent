"""会话记忆用户隔离单元测试（spec FR-003/SC-003，contracts/memory-api.md §2）。"""

from GSagent.core.memory.session import SessionMemoryStore
from GSagent.core.memory.user import safe_user_dir


def make_store(tmp_path, user: str, root=None) -> SessionMemoryStore:
    return SessionMemoryStore(root=root or tmp_path, user=user)


class TestUserIsolation:
    def test_same_session_id_not_overwritten(self, tmp_path):
        """A/B 各保存 session_id 相同的会话 → 目录按用户分隔、互不覆盖（SC-003）。"""
        store_a = make_store(tmp_path, "alice")
        store_b = make_store(tmp_path, "bob")
        assert store_a.save("cli-abc", [{"role": "user", "content": "A 的话"}])
        assert store_b.save("cli-abc", [{"role": "user", "content": "B 的话"}])

        assert store_a.session_dir("cli-abc") != store_b.session_dir("cli-abc")
        assert store_a.session_dir("cli-abc").parent == tmp_path / safe_user_dir("alice")
        assert store_b.session_dir("cli-abc").parent == tmp_path / safe_user_dir("bob")

        _, hist_a = store_a.load("cli-abc")
        _, hist_b = store_b.load("cli-abc")
        assert hist_a[0]["content"] == "A 的话"
        assert hist_b[0]["content"] == "B 的话"

    def test_list_sessions_only_own_user(self, tmp_path):
        """list_sessions 只列本用户会话（US2 验收 2）。"""
        make_store(tmp_path, "alice").save("s1", [])
        make_store(tmp_path, "bob").save("s2", [])
        assert [m["session_id"] for m in make_store(tmp_path, "alice").list_sessions()] == ["s1"]
        assert [m["session_id"] for m in make_store(tmp_path, "bob").list_sessions()] == ["s2"]

    def test_remove_does_not_affect_other_user(self, tmp_path):
        """A remove 其会话不影响 B（US2 验收 3）。"""
        store_a = make_store(tmp_path, "alice")
        store_b = make_store(tmp_path, "bob")
        store_a.save("shared-id", [{"role": "user", "content": "a"}])
        store_b.save("shared-id", [{"role": "user", "content": "b"}])
        assert store_a.remove("shared-id") is True
        _, hist_b = store_b.load("shared-id")
        assert hist_b is not None and hist_b[0]["content"] == "b"

    def test_meta_contains_user(self, tmp_path):
        """meta.json 记录 user（FR-003）。"""
        store = make_store(tmp_path, "alice")
        store.save("s1", [])
        meta, _ = store.load("s1")
        assert meta["user"] == "alice"


class TestPathSafety:
    def test_user_dir_uses_safe_encoding(self, tmp_path):
        """目录路径使用 safe_user_dir 编码（FR-007 防穿越：无 '..' 路径段）。"""
        store = SessionMemoryStore(root=tmp_path, user="../evil")
        parts = store.user_dir().parts
        assert ".." not in parts, f"路径含穿越段: {store.user_dir()}"
        assert store.user_dir() == tmp_path / safe_user_dir("../evil")
