"""Filesystem toolset - local file operations.

Provides tools for reading, listing, searching, and modifying local files.
All paths are validated to stay inside the configured root_dir (sandbox).

Write/delete operations are marked approval-required so the CLI prompts
the user before they execute.
"""

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
    """Configuration for the filesystem toolset."""

    root_dir: str = Field(
        default=".",
        description="Sandbox root directory. All file operations are restricted to this directory and its subdirectories.",
    )
    max_read_lines: int = Field(default=200, description="Max lines returned by read_file")
    encoding: str = Field(default="utf-8", description="File encoding for read/write")


def _resolve_safe_path(config: FilesystemToolConfig, path: str) -> tuple[Optional[Path], Optional[str]]:
    """Resolve a user-supplied path against the sandbox root.

    Returns:
        (resolved_path, None) on success, or (None, error_message) if the
        path escapes the sandbox root.
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
    """Get the toolset config from the invocation context."""
    toolset = getattr(context, "toolset", None)
    if toolset is not None and toolset.config is not None:
        return toolset.config
    return FilesystemToolConfig()


class ListDirectoryTool(Tool):
    """List files and subdirectories in a directory."""

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


class ReadFileTool(Tool):
    """Read the contents of a text file."""

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


class SearchFilesTool(Tool):
    """Search for files by name pattern."""

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


class FileInfoTool(Tool):
    """Get metadata about a file or directory."""

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


class WriteFileTool(Tool):
    """Write content to a file (requires approval)."""

    name: str = "write_file"
    description: str = (
        "Write or append text content to a file. Parent directories are "
        "created automatically. This modifies the filesystem and requires approval."
    )
    parameters: Dict[str, ToolParameter] = {
        "path": ToolParameter(
            type="string",
            description="File path relative to the workspace root",
            required=True,
        ),
        "content": ToolParameter(
            type="string",
            description="Text content to write",
            required=True,
        ),
        "append": ToolParameter(
            type="boolean",
            description="If true, append to the file instead of overwriting",
            required=False,
            default=False,
        ),
    }

    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        config = _get_config(context)
        path = params.get("path", "")
        content = params.get("content", "")
        append = bool(params.get("append", False))

        target, error = _resolve_safe_path(config, path)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=error, params=params
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(target, mode, encoding=config.encoding) as f:
                f.write(content)
        except OSError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to write file: {e}",
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "path": str(target),
                "bytes_written": len(content.encode(config.encoding)),
                "mode": "append" if append else "overwrite",
            },
            params=params,
        )


class DeleteFileTool(Tool):
    """Delete a file (requires approval)."""

    name: str = "delete_file"
    description: str = (
        "Delete a file. This is irreversible and requires approval. "
        "Directories cannot be deleted with this tool."
    )
    parameters: Dict[str, ToolParameter] = {
        "path": ToolParameter(
            type="string",
            description="File path relative to the workspace root",
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

        if target.is_dir():
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"'{path}' is a directory; directory deletion is not supported.",
                params=params,
            )
        if not target.is_file():
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data={"message": f"File '{path}' does not exist; nothing to delete."},
                params=params,
            )

        try:
            target.unlink()
        except OSError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to delete file: {e}",
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={"deleted": str(target)},
            params=params,
        )


def create_filesystem_toolset(
    install_config: Optional[Dict[str, Any]] = None,
) -> Toolset:
    """Create the filesystem toolset.

    Args:
        install_config: Optional config overrides (e.g. {"root_dir": "/data"}).

    Returns:
        Configured Toolset with read tools always available and
        write/delete tools requiring human approval.
    """
    config = FilesystemToolConfig(**(install_config or {}))

    return Toolset(
        name="filesystem",
        description=(
            "Local file operations: list, read, search, inspect, write, and "
            "delete files inside the workspace sandbox."
        ),
        tools=[
            ListDirectoryTool(),
            ReadFileTool(),
            SearchFilesTool(),
            FileInfoTool(),
            WriteFileTool(),
            DeleteFileTool(),
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
        approval_required_tools=["write_file", "delete_file"],
    )