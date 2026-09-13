"""工具注册与守卫/审批包装测试（T012，US2）。

验证（contracts/tools.md，SC-002/003）：
- registry 注册/查询（get_all_tools 数量一致）
- 守卫包装层拦截破坏性命令/非法路径（PathGuard/CommandGuard）+ 审计 blocked
- 审批标记（requires_approval + hitl always/never）
"""

from langchain_core.tools import tool

from GSagent.core.policy.command_guard import CommandGuard
from GSagent.core.policy.hitl import HitlPolicy
from GSagent.core.policy.path_guard import PathGuard
from GSagent.core.tools.registry import ToolRegistry


@tool
def read_file(path: str, start_line: int = 1) -> str:
    """read a text file"""
    return f"content of {path}"


@tool("bash")
def run_bash(command: str, timeout: int = 30) -> str:
    """run a bash command"""
    return f"ran: {command}"


@tool
def remember(content: str) -> str:
    """store a memory"""
    return "stored"


class TestRegistry:
    def test_register_and_query(self):
        """注册/查询/get_all_tools。"""
        reg = ToolRegistry()
        reg.register(read_file)
        reg.register(remember)
        assert len(reg) == 2
        assert reg.get_tool("read_file") is not None
        assert reg.get_tool("missing") is None
        names = {t.name for t in reg.get_all_tools()}
        assert names == {"read_file", "remember"}

    def test_path_guard_blocks_escape(self):
        """PathGuard 拦截越界路径。"""
        reg = ToolRegistry().configure(path_guard=PathGuard(workspace_root="."))
        reg.register(read_file)
        result = reg.get_tool("read_file").invoke({"path": "C:/Windows/system32/config"})
        assert "blocked" in str(result).lower()

    def test_path_guard_allows_in_root(self):
        """合法路径放行。"""
        reg = ToolRegistry().configure(path_guard=PathGuard(workspace_root="."))
        reg.register(read_file)
        result = reg.get_tool("read_file").invoke({"path": "GSagent/config.py"})
        assert "content of" in str(result)

    def test_command_guard_blocks_dangerous(self):
        """CommandGuard 拦截破坏性命令。"""
        reg = ToolRegistry().configure(command_guard=CommandGuard())
        reg.register(run_bash)
        result = reg.get_tool("bash").invoke({"command": "rm -rf /etc"})
        assert "blocked" in str(result).lower()

    def test_command_guard_allows_safe(self):
        """安全命令放行。"""
        reg = ToolRegistry().configure(command_guard=CommandGuard())
        reg.register(run_bash)
        result = reg.get_tool("bash").invoke({"command": "ls -la"})
        assert "ran:" in str(result)

    def test_no_guard_tool_passthrough(self):
        """无守卫工具（remember）原样执行。"""
        reg = ToolRegistry().configure(path_guard=PathGuard(workspace_root="."))
        reg.register(remember)
        assert "stored" in str(reg.get_tool("remember").invoke({"content": "x"}))


class TestApproval:
    def test_requires_approval_marked(self):
        """注册时标记需审批 → requires_approval 返回 True。"""
        reg = ToolRegistry()
        reg.register(run_bash, requires_approval=True)
        assert reg.requires_approval("bash", {}) is True
        assert reg.requires_approval("remember", {}) is False

    def test_hitl_always_all_approve(self):
        """hitl=always → 全部需审批。"""
        reg = ToolRegistry().configure(hitl_policy=HitlPolicy(mode="always"))
        reg.register(remember)
        assert reg.requires_approval("remember", {}) is True

    def test_hitl_never_no_approve(self):
        """hitl=never → 全部免批。"""
        reg = ToolRegistry().configure(hitl_policy=HitlPolicy(mode="never"))
        reg.register(run_bash, requires_approval=True)
        assert reg.requires_approval("bash", {}) is False
