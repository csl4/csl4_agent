"""针对（只读）文件系统工具集的测试。"""

import pytest

from agent.core.models import ToolInvokeContext
from agent.plugins.toolsets.filesystem import create_filesystem_toolset


@pytest.fixture
def toolset(tmp_path):
    (tmp_path / "demo.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.py").write_text("print('hi')\n", encoding="utf-8")
    return create_filesystem_toolset(install_config={"root_dir": str(tmp_path)})


@pytest.fixture
def approved_ctx(toolset):
    return ToolInvokeContext(user_approved=True, toolset=toolset)


def _get_tool(toolset, name):
    return next(t for t in toolset.tools if t.name == name)


def test_list_directory(toolset, approved_ctx):
    result = _get_tool(toolset, "list_directory").invoke({"path": "."}, approved_ctx)
    assert result.status.value == "success"
    names = {e["name"] for e in result.data["entries"]}
    assert names == {"demo.txt", "sub"}


def test_list_directory_with_pattern(toolset, approved_ctx):
    result = _get_tool(toolset, "list_directory").invoke(
        {"path": ".", "pattern": "*.txt"}, approved_ctx
    )
    assert result.status.value == "success"
    assert [e["name"] for e in result.data["entries"]] == ["demo.txt"]


def test_list_directory_missing(toolset, approved_ctx):
    result = _get_tool(toolset, "list_directory").invoke({"path": "nope"}, approved_ctx)
    assert result.status.value == "error"


def test_read_file_pagination(toolset, approved_ctx):
    tool = _get_tool(toolset, "read_file")
    result = tool.invoke(
        {"path": "demo.txt", "start_line": 2, "max_lines": 1}, approved_ctx
    )
    assert result.status.value == "success"
    assert result.data["content"] == "line2"
    assert result.data["truncated"] is True
    assert result.data["total_lines"] == 3


def test_read_file_missing(toolset, approved_ctx):
    result = _get_tool(toolset, "read_file").invoke({"path": "nope.txt"}, approved_ctx)
    assert result.status.value == "error"


def test_search_files(toolset, approved_ctx):
    result = _get_tool(toolset, "search_files").invoke({"pattern": "*.py"}, approved_ctx)
    assert result.status.value == "success"
    assert result.data["matches"] == ["sub/nested.py"]


def test_search_files_no_match(toolset, approved_ctx):
    result = _get_tool(toolset, "search_files").invoke({"pattern": "*.rs"}, approved_ctx)
    assert result.status.value == "no_data"


def test_file_info(toolset, approved_ctx, tmp_path):
    result = _get_tool(toolset, "file_info").invoke({"path": "demo.txt"}, approved_ctx)
    assert result.status.value == "success"
    assert result.data["size_bytes"] == (tmp_path / "demo.txt").stat().st_size


def test_sandbox_escape_blocked(toolset, approved_ctx):
    for tool_name in ("read_file", "list_directory", "search_files", "file_info"):
        result = _get_tool(toolset, tool_name).invoke(
            {"path": "../outside.txt"}, approved_ctx
        )
        assert result.status.value == "error", tool_name
        assert "outside the allowed root" in result.error