"""记忆工具集用户隔离单元测试（spec FR-005/SC-005，002-langchain-ecosystem）。"""

from GSagent.plugins.toolsets.memory.lc_tools import create_memory_tools


def make_tools(tmp_path, user: str, scope: str = "proj-x"):
    """同一 db 路径 + 不同 user → 隔离由 user 维度实现（非 db 隔离）。"""
    return create_memory_tools(
        {"db_path": str(tmp_path / "shared.db"), "user": user, "scope": scope}
    )


def remember(tools, content: str):
    return tools[0].invoke({"content": content})


def search(tools, query: str):
    return tools[1].invoke({"query": query})


class TestToolsetUserIsolation:
    def test_remember_search_isolated_by_user(self, tmp_path):
        """同一 store（同 db）不同 user 配置 → remember/search 各自隔离（SC-005）。"""
        tools_a = make_tools(tmp_path, "alice")
        tools_b = make_tools(tmp_path, "bob")
        assert "remembered" in remember(tools_a, "A 的机密")
        assert "remembered" in remember(tools_b, "B 的机密")

        res_a = search(tools_a, "机密")
        res_b = search(tools_b, "机密")
        assert "A 的机密" in res_a and "B 的机密" not in res_a
        assert "B 的机密" in res_b and "A 的机密" not in res_b

    def test_same_content_not_shared_across_users(self, tmp_path):
        tools_a = make_tools(tmp_path, "alice")
        tools_b = make_tools(tmp_path, "bob")
        assert "remembered" in remember(tools_a, "共同事实")
        assert "remembered" in remember(tools_b, "共同事实")  # 各自成条
        assert search(tools_a, "共同事实").count("共同事实") == 1
        assert search(tools_b, "共同事实").count("共同事实") == 1

    def test_default_user_is_system(self, tmp_path, monkeypatch):
        """工具不配 user → 自动系统用户（默认零迁移）。"""
        import getpass

        monkeypatch.setattr(getpass, "getuser", lambda: "sysuser")
        tools = create_memory_tools({"db_path": str(tmp_path / "d.db"), "scope": "p"})
        remember(tools, "系统用户记忆")
        assert "系统用户记忆" in search(tools, "系统用户记忆")
