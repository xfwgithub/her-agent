"""MCP-based terminal backend.

Executes shell commands through an MCP server that provides shell/exec
capabilities (e.g. ``@anthropic/mcp-server-shell`` or a custom bash MCP
server).

The backend auto-detects which MCP server provides shell commands by
scanning connected servers for tools matching ``shell``, ``bash``,
``execute_command``, etc.

When no suitable MCP server is connected, falls back to a no-op that
raises a clear error rather than silently failing.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# MCP tool name patterns that indicate shell execution capability
_SHELL_TOOL_PATTERNS = {
    "shell", "bash", "execute_command", "run_command", "exec",
    "terminal", "command",
}


def _find_shell_server() -> tuple[str, str] | None:
    """Find an MCP server that provides shell execution.

    Returns (server_name, tool_name) or None.
    """
    try:
        from tools.mcp_tool import _servers
        for srv_name, server in _servers.items():
            if not hasattr(server, "_tools"):
                continue
            for tool in server._tools:
                tname = tool.name.lower()
                # Try exact match first, then suffix/prefix match
                if tname in _SHELL_TOOL_PATTERNS:
                    return srv_name, tool.name
                for pattern in _SHELL_TOOL_PATTERNS:
                    if tname.endswith(f"_{pattern}") or tname.startswith(f"{pattern}_"):
                        return srv_name, tool.name
    except Exception:
        pass
    return None


def _call_mcp_shell(server_name: str, tool_name: str, command: str) -> dict:
    """Execute a command via an MCP server's shell tool."""
    try:
        from tools.mcp_tool import _run_on_mcp_loop, _servers

        async def _do_call():
            server = _servers.get(server_name)
            if server is None:
                raise RuntimeError(f"MCP server '{server_name}' not connected")
            result = await server.session.call_tool(
                tool_name, arguments={"command": command}
            )
            return result

        raw = _run_on_mcp_loop(_do_call, timeout=180)
    except Exception as e:
        return {"output": f"error: MCP shell call failed: {e}", "returncode": -1}

    # Parse MCP result
    output_parts = []
    if hasattr(raw, "content"):
        for item in raw.content:
            text = ""
            if hasattr(item, "text"):
                text = item.text
            elif isinstance(item, dict):
                text = item.get("text", "")
            if text:
                output_parts.append(text)
    output = "\n".join(output_parts)

    return {"output": output, "returncode": 0}


class McpTerminalBackend:
    """Duck-typed terminal backend that delegates to an MCP shell server.

    Implements the subset of ``BaseEnvironment`` that ``terminal_tool.py``
    actually uses: ``execute()``, ``cwd``, ``cleanup()``.
    """

    def __init__(self, cwd: str | None = None):
        self._cwd = cwd or os.getcwd()
        self._server_info = _find_shell_server()

    @property
    def cwd(self) -> str:
        return self._cwd

    @cwd.setter
    def cwd(self, value: str) -> None:
        self._cwd = value

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
        rewrite_compound_background: bool = True,
    ) -> dict:
        """Execute a command via MCP shell server.

        Returns ``{"output": str, "returncode": int}``.
        """
        if self._server_info is None:
            return {
                "output": (
                    "MCP terminal backend requires a shell-capable MCP server. "
                    "Connect one via config.yaml mcp_servers and try again."
                ),
                "returncode": -1,
            }

        if not command.strip():
            return {"output": "", "returncode": 0}

        server_name, tool_name = self._server_info

        effective_cwd = cwd or self._cwd
        wrapped = f"cd {_shquote(effective_cwd)} && {command}"

        return _call_mcp_shell(server_name, tool_name, wrapped)

    def cleanup(self) -> None:
        """No-op for MCP backend (server manages its own lifecycle)."""
        pass

    def stop(self) -> None:
        """Alias for cleanup."""
        self.cleanup()


def _shquote(s: str) -> str:
    """Minimal shell quoting."""
    if not s:
        return "''"
    if all(c.isalnum() or c in '/._-~' for c in s):
        return s
    escaped = s.replace("'", "'\\''")
    return f"'{escaped}'"
