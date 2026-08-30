"""文件系统工具集——本地文件操作。

提供用于列目录、读取、搜索和检视本地文件的只读工具。所有路径都会被校验，
确保不会超出配置的 root_dir（沙箱）。

此处刻意不提供文件修改能力：请使用 bash 工具集，它依据前缀白/黑名单校验命令，
并对任何未显式放行的操作要求用户审批。
"""

# ======================= 中文导览 =======================
# 文件系统工具集（只读）：列目录 / 读文件 / 搜文件 / 查元数据。
#   每个工具皆是 Tool 子类，只实现 _invoke()。
# 安全设计：所有路径经 _resolve_safe_path() 校验【不得越出 root_dir 沙箱】；
#          刻意不提供写/删——写删走 bash 工具集（带前缀白名单+人工审批）。
# create_filesystem_toolset() 是登记进 BUILTIN_PYTHON_TOOLSETS 的工厂，tags=[CLI]。
# =========================================================

import fnmatch
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field

from agent.core.models import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)
from agent.core.tools import (
    CallablePrerequisite,
    Tool,
    Transformer,
    Toolset,
    ToolsetTag,
    ToolsetType,
)
from agent.core.transformers.builtin import JsonTruncationTransformer
from agent.utils.pydantic_utils import ToolsetConfig


class FilesystemToolConfig(ToolsetConfig):
    """文件系统工具集的配置。"""

    root_dir: str = Field(
        default=".",
        description="Sandbox root directory. All file operations are restricted to this directory and its subdirectories.",
    )
    max_read_lines: int = Field(default=200, description="Max lines returned by read_file")
    encoding: str = Field(default="utf-8", description="File encoding for read/write")


def _resolve_safe_path(config: FilesystemToolConfig, path: str) -> tuple[Optional[Path], Optional[str]]:
    """在沙箱根目录下解析用户提供的路径。

    返回:
        成功时返回 (resolved_path, None)，若路径越出沙箱根目录则返回
        (None, error_message)。
    """
    root = Path(config.root_dir).resolve()
    target = (root / path).resolve() if path else root

    if target != root and root not in target.parents:
        return None, (
            f"Path '{path}' is outside the allowed root directory '{root}'. "
            "Access denied."
        )
    return target, None


def _get_config(context: ToolInvokeContext) -> FilesystemToolConfig:
    """从调用上下文中获取工具集配置。"""
    toolset = getattr(context, "toolset", None)
    if toolset is not None and toolset.config is not None:
        return toolset.config
    return FilesystemToolConfig()


# ---- 只读工具：列目录（输入 path+可选 pattern → 输出条目清单）----
class ListDirectoryTool(Tool):
    """列出目录中的文件和子目录。"""

    name: str = "list_directory"
    description: str = (
        "List files and subdirectories in a directory. Returns name, type "
        "(file/dir), and size in bytes for each entry."
    )
    parameters: Dict[str, ToolParameter] = {
        "path": ToolParameter(
            type="string",
            description="Directory path relative to the workspace root (default: root itself)",
            required=False,
            default=".",
        ),
        "pattern": ToolParameter(
            type="string",
            description="Optional fnmatch pattern to filter entries by name (e.g. '*.py')",
            required=False,
            default=None,
        ),
    }
    transformers: Optional[List[Transformer]] = Field(
        default_factory=lambda: [JsonTruncationTransformer(max_list_items=100)]
    )

    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        config = _get_config(context)
        path = params.get("path") or "."
        pattern = params.get("pattern")

        target, error = _resolve_safe_path(config, path)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=error, params=params
            )

        if not target.is_dir():
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"'{path}' is not a directory or does not exist.",
                params=params,
            )

        entries = []
        try:
            for entry in sorted(target.iterdir(), key=lambda e: e.name):
                if pattern and not fnmatch.fnmatch(entry.name, pattern):
                    continue
                try:
                    stat = entry.stat()
                    entries.append(
                        {
                            "name": entry.name,
                            "type": "dir" if entry.is_dir() else "file",
                            "size_bytes": stat.st_size if entry.is_file() else None,
                        }
                    )
                except OSError:
                    continue
        except OSError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list directory: {e}",
                params=params,
            )

        if not entries:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data={"entries": [], "message": f"No entries found in '{path}'."},
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={"directory": str(target), "count": len(entries), "entries": entries},
            params=params,
        )


# ---- 只读工具：读文件（输入 path+start_line+max_lines → 输出文本内容，分页截断）----
class ReadFileTool(Tool):
    """读取文本文件的内容。"""

    name: str = "read_file"
    description: str = (
        "Read the contents of a text file. Returns the raw text, optionally "
        "starting from a given line and limited to max_lines."
    )
    parameters: Dict[str, ToolParameter] = {
        "path": ToolParameter(
            type="string",
            description="File path relative to the workspace root",
            required=True,
        ),
        "start_line": ToolParameter(
            type="integer",
            description="Line number to start reading from (1-based)",
            required=False,
            default=1,
        ),
        "max_lines": ToolParameter(
            type="integer",
            description="Maximum number of lines to return",
            required=False,
            default=None,
        ),
    }

    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        config = _get_config(context)
        path = params.get("path", "")

        target, error = _resolve_safe_path(config, path)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=error, params=params
            )

        if not target.is_file():
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"File '{path}' does not exist or is not a regular file.",
                params=params,
            )

        start_line = max(int(params.get("start_line") or 1), 1)
        max_lines = params.get("max_lines") or config.max_read_lines
        max_lines = int(max_lines)

        try:
            content = target.read_text(encoding=config.encoding, errors="replace")
        except OSError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to read file: {e}",
                params=params,
            )

        lines = content.splitlines()
        total = len(lines)
        selected = lines[start_line - 1 : start_line - 1 + max_lines]

        if not selected:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data={"message": f"No content at line {start_line} (file has {total} lines)."},
                params=params,
            )

        text = "\n".join(selected)
        truncated = start_line - 1 + max_lines < total

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "path": str(target),
                "content": text,
                "start_line": start_line,
                "lines_returned": len(selected),
                "total_lines": total,
                "truncated": truncated,
            },
            params=params,
        )


# ---- 只读工具：按名模式搜文件（输入 pattern+path → 输出匹配路径列表，上限200）----
# 挂 JsonTruncationTransformer 兜底防超大结果。
class SearchFilesTool(Tool):
    """按名称模式搜索文件。"""

    name: str = "search_files"
    description: str = (
        "Recursively search for files whose names match an fnmatch pattern "
        "(e.g. '*.py', 'test_*'). Returns matching paths relative to the search root."
    )
    parameters: Dict[str, ToolParameter] = {
        "pattern": ToolParameter(
            type="string",
            description="fnmatch pattern to match file names against",
            required=True,
        ),
        "path": ToolParameter(
            type="string",
            description="Directory to search in, relative to the workspace root (default: root)",
            required=False,
            default=".",
        ),
    }
    transformers: Optional[List[Transformer]] = Field(
        default_factory=lambda: [JsonTruncationTransformer(max_list_items=100)]
    )

    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        config = _get_config(context)
        pattern = params.get("pattern", "")
        path = params.get("path") or "."

        root, error = _resolve_safe_path(config, path)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=error, params=params
            )
        if not root.is_dir():
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"'{path}' is not a directory or does not exist.",
                params=params,
            )

        matches: List[str] = []
        try:
            for current, dirnames, filenames in os.walk(root):
                # Match against both files and directories
                for name in list(dirnames) + filenames:
                    if fnmatch.fnmatch(name, pattern):
                        rel = os.path.relpath(os.path.join(current, name), root)
                        matches.append(rel.replace("\\", "/"))
                        if len(matches) >= 200:
                            return StructuredToolResult(
                                status=StructuredToolResultStatus.SUCCESS,
                                data={
                                    "matches": matches,
                                    "count": len(matches),
                                    "truncated": True,
                                    "message": "Result capped at 200 matches; use a narrower pattern.",
                                },
                                params=params,
                            )
        except OSError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Search failed: {e}",
                params=params,
            )

        if not matches:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data={"matches": [], "message": f"No files matching '{pattern}' found."},
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={"matches": sorted(matches), "count": len(matches), "truncated": False},
            params=params,
        )


# ---- 只读工具：查文件/目录元数据（输入 path → 输出 size/时间戳/类型）----
class FileInfoTool(Tool):
    """获取文件或目录的元数据。"""

    name: str = "file_info"
    description: str = "Get metadata (size, timestamps, permissions) about a file or directory."

    parameters: Dict[str, ToolParameter] = {
        "path": ToolParameter(
            type="string",
            description="File or directory path relative to the workspace root",
            required=True,
        ),
    }

    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        config = _get_config(context)
        path = params.get("path", "")

        target, error = _resolve_safe_path(config, path)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=error, params=params
            )

        if not target.exists():
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data={"message": f"'{path}' does not exist."},
                params=params,
            )

        try:
            stat = target.stat()
        except OSError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to stat: {e}",
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "path": str(target),
                "type": "dir" if target.is_dir() else "file",
                "size_bytes": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
            },
            params=params,
        )


def create_filesystem_toolset(
    install_config: Optional[Dict[str, Any]] = None,
) -> Toolset:
    """创建文件系统工具集（只读工具）。

    参数:
        install_config: 可选的配置覆盖（例如 {"root_dir": "/data"}）。

    返回:
        配置好只读文件工具的 Toolset。文件修改由 bash 工具集结合校验与
        审批处理。
    """
    config = FilesystemToolConfig(**(install_config or {}))

    return Toolset(
        name="filesystem",
        description=(
            "Read-only local file operations: list, read, search, and inspect "
            "files inside the workspace sandbox."
        ),
        tools=[
            ListDirectoryTool(),
            ReadFileTool(),
            SearchFilesTool(),
            FileInfoTool(),
        ],
        prerequisites=[
            CallablePrerequisite(
                name="root_dir_exists",
                callable=lambda cfg: Path(cfg.root_dir).exists(),
            ),
        ],
        config=config,
        type=ToolsetType.PYTHON,
        tags=[ToolsetTag.CLI],
    )