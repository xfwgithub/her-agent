"""MCP-based file operations — delegates to @modelcontextprotocol/server-filesystem.

Requires:
  1. MCP filesystem server configured in ~/.her/config.yaml under mcp_servers
  2. The MCP server to be connected (tools.mcp_tool handles lifecycle)

Usage in config.yaml::

    mcp_servers:
      filesystem:
        command: "npx"
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/xinfuwei"]
        timeout: 120
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from . import (
    FileOperations,
    PatchResult,
    ReadResult,
    SearchMatch,
    SearchResult,
    WriteResult,
)


# ---------------------------------------------------------------------------
# Utility: call an MCP tool synchronously
# ---------------------------------------------------------------------------

_MCP_SERVER_NAME = "filesystem"


def _call_mcp(method: str, **params) -> Any:
    """Call an MCP tool on the filesystem server and return parsed result.

    Raises RuntimeError if the server isn't connected or the call fails.
    """
    try:
        from tools.mcp_tool import _run_on_mcp_loop

        async def _do_call():
            # Access the server task via module internals
            from tools.mcp_tool import _servers
            server = _servers.get(_MCP_SERVER_NAME)
            if server is None:
                raise RuntimeError(
                    f"MCP server '{_MCP_SERVER_NAME}' not connected. "
                    f"Check config.yaml mcp_servers section."
                )
            result = await server.session.call_tool(method, arguments=params)
            return result

        raw = _run_on_mcp_loop(_do_call, timeout=120)
    except Exception as e:
        raise RuntimeError(f"MCP call '{method}' failed: {e}") from e

    # Parse MCP result content
    if hasattr(raw, "content"):
        texts = []
        for item in raw.content:
            if hasattr(item, "text") and item.text:
                texts.append(item.text)
            elif isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)
    return str(raw)


def _mcp_available() -> bool:
    """Check if the MCP filesystem server is connected."""
    try:
        from tools.mcp_tool import _servers
        return _MCP_SERVER_NAME in _servers
    except Exception:
        return False


# ---------------------------------------------------------------------------
# MCP Backend
# ---------------------------------------------------------------------------


class McpFileOperations(FileOperations):
    """File operations via MCP filesystem server — no shell forking."""

    def __init__(self, cwd: str | None = None):
        self._cwd = Path(cwd or os.getcwd()).resolve()

    def _resolve(self, path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = self._cwd / p
        return str(p.resolve())

    # -- read_file ---------------------------------------------------------

    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        abspath = self._resolve(path)
        try:
            result = _call_mcp("filesystem_read", path=abspath)
        except RuntimeError as e:
            return ReadResult(error=str(e))

        lines = result.splitlines(keepends=True)
        total = len(lines)
        start = max(0, offset - 1)
        end = min(total, start + limit)
        selected = lines[start:end]
        numbered = "".join(
            f"{start + i + 1}|{line}" for i, line in enumerate(selected)
        )

        # MCP doesn't return file size; measure locally
        file_size = 0
        try:
            file_size = Path(abspath).stat().st_size
        except OSError:
            pass

        return ReadResult(
            content=numbered or result,
            total_lines=total,
            file_size=file_size,
            truncated=end < total,
        )

    # -- read_file_raw -----------------------------------------------------

    def read_file_raw(self, path: str) -> ReadResult:
        abspath = self._resolve(path)
        try:
            result = _call_mcp("filesystem_read", path=abspath)
        except RuntimeError as e:
            return ReadResult(error=str(e))
        return ReadResult(
            content=result,
            total_lines=result.count("\n") + 1 if result else 0,
        )

    # -- write_file --------------------------------------------------------

    def write_file(self, path: str, content: str) -> WriteResult:
        abspath = self._resolve(path)
        # MCP filesystem server's write atomically
        try:
            _call_mcp("filesystem_write", path=abspath, content=content)
        except RuntimeError as e:
            return WriteResult(error=str(e))
        return WriteResult(bytes_written=len(content.encode("utf-8")))

    # -- patch_replace -----------------------------------------------------

    def patch_replace(
        self, path: str, old_string: str, new_string: str,
        replace_all: bool = False,
    ) -> PatchResult:
        abspath = self._resolve(path)
        try:
            # MCP filesystem server has an edit tool with diff
            result = _call_mcp(
                "filesystem_edit",
                file_path=abspath,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
        except RuntimeError as e:
            return PatchResult(success=False, error=str(e))

        return PatchResult(
            success=True,
            diff=result or "edit applied",
            files_modified=[abspath],
        )

    # -- patch_v4a ---------------------------------------------------------

    def patch_v4a(self, patch_content: str) -> PatchResult:
        """Delegate V4A to read + patch_replace per file."""
        # Parse files and old/new strings from V4A format
        current_file = None
        files: dict[str, list[tuple[str, str]]] = {}
        old_lines: list[str] = []
        new_lines: list[str] = []

        for line in patch_content.splitlines():
            if line.startswith("*** Update File:"):
                current_file = line.split(":", 1)[1].strip()
            elif line.startswith("-") and not line.startswith("---"):
                old_lines.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++"):
                new_lines.append(line[1:])

        # Apply per-file
        for fpath, _ in files.items():
            result = self.patch_replace(fpath, "\n".join(old_lines), "\n".join(new_lines))
            if not result.success:
                return result

        return PatchResult(success=True, files_modified=list(files.keys()))

    # -- delete_file -------------------------------------------------------

    def delete_file(self, path: str) -> WriteResult:
        abspath = self._resolve(path)
        try:
            _call_mcp("filesystem_delete", path=abspath)
        except RuntimeError as e:
            return WriteResult(error=str(e))
        return WriteResult(bytes_written=0)

    # -- delete_path -------------------------------------------------------

    def delete_path(self, path: str, recursive: bool = False) -> WriteResult:
        abspath = self._resolve(path)
        try:
            if recursive:
                _call_mcp("filesystem_delete_recursive", path=abspath)
            else:
                _call_mcp("filesystem_delete", path=abspath)
        except RuntimeError as e:
            return WriteResult(error=str(e))
        return WriteResult(bytes_written=0)

    # -- move_file ---------------------------------------------------------

    def move_file(self, src: str, dst: str) -> WriteResult:
        src_abs = self._resolve(src)
        dst_abs = self._resolve(dst)
        try:
            _call_mcp("filesystem_move", source=src_abs, destination=dst_abs)
        except RuntimeError as e:
            return WriteResult(error=str(e))
        return WriteResult(bytes_written=0)

    # -- search ------------------------------------------------------------

    def search(
        self, pattern: str, path: str = ".", target: str = "content",
        file_glob: Optional[str] = None, limit: int = 50, offset: int = 0,
        output_mode: str = "content", context: int = 0,
    ) -> SearchResult:
        abspath = self._resolve(path)
        # MCP filesystem server doesn't have a native search tool,
        # so fall back to listing files + searching. For now, use
        # the python_native backend for search operations.
        from .python_native import PythonNativeFileOperations
        native = PythonNativeFileOperations(cwd=str(self._cwd))
        return native.search(
            pattern, path=abspath, target=target,
            file_glob=file_glob, limit=limit, offset=offset,
            output_mode=output_mode, context=context,
        )
