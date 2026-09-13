"""用户身份解析单元测试（spec FR-001，contracts/user-key.md）。"""

import getpass

import pytest

from GSagent.core.memory.user import resolve_user_key, safe_user_dir


class TestResolveUserKey:
    def test_default_is_system_user(self, monkeypatch):
        """默认返回系统登录用户名（SC-004 零迁移前提）。"""
        monkeypatch.setattr(getpass, "getuser", lambda: "bob")
        assert resolve_user_key() == "bob"

    def test_cfg_user_overrides(self, monkeypatch):
        """显式 cfg_user 覆盖系统用户。"""
        monkeypatch.setattr(getpass, "getuser", lambda: "bob")
        assert resolve_user_key(cfg_user="alice") == "alice"
        assert resolve_user_key(cfg_user="  alice  ") == "alice"

    def test_blank_cfg_user_falls_through(self, monkeypatch):
        """空白 cfg_user 等同未传，走系统用户。"""
        monkeypatch.setattr(getpass, "getuser", lambda: "bob")
        assert resolve_user_key(cfg_user="") == "bob"
        assert resolve_user_key(cfg_user="   ") == "bob"

    def test_identity_highest_priority(self, monkeypatch):
        """serve API 身份（预留）最高优先。"""
        monkeypatch.setattr(getpass, "getuser", lambda: "bob")
        assert resolve_user_key(cfg_user="alice", identity="api-1") == "api-1"

    def test_getpass_failure_falls_back_to_local(self, monkeypatch):
        """getpass 失败 → 确定性回退 'local'，绝不抛错（spec Edge Case）。"""
        def boom() -> str:
            raise OSError("no user env")

        monkeypatch.setattr(getpass, "getuser", boom)
        assert resolve_user_key() == "local"


class TestSafeUserDir:
    def test_normal_user_unchanged(self):
        assert safe_user_dir("alice") == "alice"
        assert safe_user_dir("alice.bob-x_1") == "alice.bob-x_1"

    def test_path_chars_replaced(self):
        """路径特殊字符替换为 _，防穿越（spec FR-007）。"""
        assert "/" not in safe_user_dir("../etc")
        assert safe_user_dir("../etc") == ".._etc"
        assert "\\" not in safe_user_dir("a\\b")

    def test_forbidden_dir_rejected(self):
        """空 / '.' / '..' → '_local' 兜底。"""
        assert safe_user_dir("") == "_local"
        assert safe_user_dir(".") == "_local"
        assert safe_user_dir("..") == "_local"

    def test_roundtrip_deterministic(self):
        assert safe_user_dir("用户 名") == "用户_名"
