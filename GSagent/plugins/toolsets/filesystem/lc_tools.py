"""filesystem 工具集（langchain @tool 版，002-langchain-ecosystem）。

4 个只读工具迁移为 ``@tool``：list_directory / read_file / search_files / file_info。
- 路径沙箱：所有路径解析后必须落在 ``root_dir`` 内（``_safe_path``），越界抛错
  （对应既有 ``_resolve_safe_path`` 语义）。
- 配置经工厂闭包注入（root_dir / max_read_lines / encoding），不暴露为工具参数。
- 返回 JSON 文本（对齐既有 StructuredToolResult.data 形态）。

守卫：工具名命中 PATH_TOOLS（list_directory/read_file/search_files/file_info），
注册进 ToolRegistry 时自动挂 PathGuard（wrap_with_guards）。
"""

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool


def _safe_path(root_dir: str, user_path: str) -> Path:
    """解析路径并校验落在 root_dir 内（越界抛 ValueError）。"""
    root = Path(root_dir).expanduser().resolve()
    raw = Path(user_path).expanduser()
    target = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Path '{user_path}' resolves outside the workspace root '{root}'.")
    return target


def _load_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = config or {}
    return {
        "root_dir": str(cfg.get("root_dir") or "."),
        "max_read_lines": int(cfg.get("max_read_lines", 200) or 200),
        "encoding": str(cfg.get("encoding") or "utf-8"),
    }


def create_filesystem_tools(config: Optional[Dict[str, Any]] = None) -> List[Any]:
    """工厂：返回 filesystem @tool 列表（配置闭包注入）。"""
    cfg = _load_config(config)
    root_dir = cfg["root_dir"]
    max_read_lines = cfg["max_read_lines"]
    encoding = cfg["encoding"]

    @tool
    def list_directory(path: str = ".", pattern: str = "") -> str:
        """List files and subdirectories in a directory (name, type, size)."""
        target = _safe_path(root_dir, path)
        entries = []
        for p in sorted(target.iterdir()):
            if pattern and not fnmatch.fnmatch(p.name, pattern):
                continue
            try:
                st = p.stat()
                entries.append(
                    {
                        "name": p.name,
                        "type": "dir" if p.is_dir() else "file",
                        "size_bytes": st.st_size,
                    }
                )
            except OSError:
                entries.append({"name": p.name, "type": "unknown", "size_bytes": 0})
        return json.dumps(
            {"directory": str(target), "count": len(entries), "entries": entries[:100]},
            ensure_ascii=False,
        )

    @tool
    def read_file(path: str, start_line: int = 1, max_lines: Optional[int] = None) -> str:
        """Read a text file with optional pagination (start_line 1-based)."""
        target = _safe_path(root_dir, path)
        text = Path(target).read_text(encoding=encoding, errors="replace")
        lines = text.splitlines()
        limit = max_lines if max_lines is not None else max_read_lines
        start = max(1, start_line)
        sliced = lines[start - 1 : start - 1 + limit]
        return json.dumps(
            {
                "path": str(target),
                "content": "\n".join(sliced),
                "start_line": start,
                "lines_returned": len(sliced),
                "total_lines": len(lines),
                "truncated": start - 1 + limit < len(lines),
            },
            ensure_ascii=False,
        )

    @tool
    def search_files(pattern: str, path: str = ".") -> str:
        """Recursively search files by fnmatch pattern under a directory."""
        root = Path(root_dir).expanduser().resolve()
        base = _safe_path(root_dir, path)
        matches: List[str] = []
        truncated = False
        for dirpath, dirnames, filenames in os.walk(base):
            for name in filenames + dirnames:
                if fnmatch.fnmatch(name, pattern):
                    rel = os.path.relpath(os.path.join(dirpath, name), root)
                    matches.append(rel)
                    if len(matches) >= 200:
                        truncated = True
                        break
            if truncated:
                break
        return json.dumps(
            {"matches": matches, "count": len(matches), "truncated": truncated},
            ensure_ascii=False,
        )

    @tool
    def file_info(path: str) -> str:
        """Get file/directory metadata (size, timestamps, type)."""
        target = _safe_path(root_dir, path)
        st = Path(target).stat()
        return json.dumps(
            {
                "path": str(target),
                "type": "dir" if Path(target).is_dir() else "file",
                "size_bytes": st.st_size,
                "modified": st.st_mtime,
                "created": st.st_ctime,
            },
            ensure_ascii=False,
        )

    return [list_directory, read_file, search_files, file_info]


__all__ = ["create_filesystem_tools"]
