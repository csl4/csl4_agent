"""记忆工具集用户隔离单元测试（spec FR-005/SC-005）。"""

from GSagent.core.models import ToolInvokeContext
from GSagent.plugins.toolsets.memory.toolset import create_memory_toolset


def make_toolset(tmp_path, user: str, scope: str = "proj-x"):
    """同一 db 路径 + 不同 user → 隔离由 user 维度实现（非 db 隔离）。"""
    return create_memory_toolset(
        {"db_path": str(tmp_path / "shared.db"), "user": user, "scope": scope}
    )


def remember(toolset, content: str):
    return toolset.tools[0]._invoke({"content": content}, ToolInvokeContext(toolset=toolset))


def search(toolset, query: str):
    return toolset.tools[1]._invoke({"query": query}, ToolInvokeContext(toolset=toolset))


class TestToolsetUserIsolation:
    def test_remember_search_isolated_by_user(self, tmp_path):
        """同一 store（同 db）不同 user 配置 → remember/search 各自隔离（SC-005）。"""
        ts_a = make_toolset(tmp_path, "alice")
        ts_b = make_toolset(tmp_path, "bob")
        assert remember(ts_a, "A 的机密").data["status"] == "saved"
        assert remember(ts_b, "B 的机密").data["status"] == "saved"

        res_a = search(ts_a, "机密")
        res_b = search(ts_b, "机密")
        assert res_a.data["count"] == 1
        assert res_a.data["results"][0]["content"] == "A 的机密"
        assert res_b.data["count"] == 1
        assert res_b.data["results"][0]["content"] == "B 的机密"

    def test_same_content_not_shared_across_users(self, tmp_path):
        ts_a = make_toolset(tmp_path, "alice")
        ts_b = make_toolset(tmp_path, "bob")
        assert remember(ts_a, "共同事实").data["status"] == "saved"
        assert remember(ts_b, "共同事实").data["status"] == "saved"  # 各自成条
        assert search(ts_a, "共同事实").data["count"] == 1
        assert search(ts_b, "共同事实").data["count"] == 1

    def test_default_user_is_system(self, tmp_path, monkeypatch):
        """工具集不配 user → 自动系统用户（默认零迁移）。"""
        import getpass

        monkeypatch.setattr(getpass, "getuser", lambda: "sysuser")
        ts = create_memory_toolset({"db_path": str(tmp_path / "d.db"), "scope": "p"})
        assert ts.config.user == "sysuser"
