"""记忆工具集用户隔离单元测试（spec FR-005/SC-005，纯 langgraph 重构）。

store 经 ``InjectedStore`` 图注入（此处直接显式传 InMemoryStore）；隔离由
user 维度（namespace）实现（非 store 实例隔离）。
"""

from langgraph.store.memory import InMemoryStore

from GSagent.plugins.toolsets.memory.lc_tools import create_memory_tools


def make_tools(user: str, scope: str = "proj-x"):
    """不同 user 配置 → 隔离由 user 维度实现（同一 InMemoryStore 共享）。"""
    return create_memory_tools({"user": user, "scope": scope})


def remember(tools, content: str, store: InMemoryStore):
    return tools[0].invoke({"content": content, "store": store})


def search(tools, query: str, store: InMemoryStore):
    return tools[1].invoke({"query": query, "store": store})


class TestToolsetUserIsolation:
    def test_remember_search_isolated_by_user(self):
        """同一 store 不同 user 配置 → remember/search 各自隔离（SC-005）。"""
        store = InMemoryStore()
        tools_a = make_tools("alice")
        tools_b = make_tools("bob")
        assert "remembered" in remember(tools_a, "A 的机密", store)
        assert "remembered" in remember(tools_b, "B 的机密", store)

        res_a = search(tools_a, "机密", store)
        res_b = search(tools_b, "机密", store)
        assert "A 的机密" in res_a and "B 的机密" not in res_a
        assert "B 的机密" in res_b and "A 的机密" not in res_b

    def test_same_content_not_shared_across_users(self):
        store = InMemoryStore()
        tools_a = make_tools("alice")
        tools_b = make_tools("bob")
        assert "remembered" in remember(tools_a, "共同事实", store)
        assert "remembered" in remember(tools_b, "共同事实", store)  # 各自成条
        assert search(tools_a, "共同事实", store).count("共同事实") == 1
        assert search(tools_b, "共同事实", store).count("共同事实") == 1

    def test_default_user_is_system(self, monkeypatch):
        """工具不配 user → 自动系统用户（默认零迁移）。"""
        import getpass

        monkeypatch.setattr(getpass, "getuser", lambda: "sysuser")
        store = InMemoryStore()
        tools = create_memory_tools({"scope": "p"})
        remember(tools, "系统用户记忆", store)
        assert "系统用户记忆" in search(tools, "系统用户记忆", store)
