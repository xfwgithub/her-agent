"""Resolve the best file operations backend for the current environment.

Selection strategy (``file_backend`` in config.yaml):

  ``"auto"`` (default)
    1. MCP filesystem server connected → McpFileOperations
    2. Terminal backend is local (no Docker/SSH) → PythonNativeFileOperations
    3. Fallback → ShellFileOperations

  ``"native"`` → always PythonNativeFileOperations (fail if not local)

  ``"mcp"`` → always McpFileOperations (fail if not connected)

  ``"shell"`` → always ShellFileOperations (legacy, always works)

Usage::

    from tools.backends.resolver import resolve_file_operations

    ops = resolve_file_operations(terminal_env=env)
    result = ops.read_file("/path/to/file")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from tools.file_operations import FileOperations, ShellFileOperations

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def _mcp_backend_available() -> bool:
    """Check if the MCP filesystem server is connected."""
    try:
        from tools.mcp_tool import _servers
        return "filesystem" in _servers
    except Exception:
        return False


def _is_local_backend(terminal_env) -> bool:
    """Heuristic: check if the terminal backend is local (no Docker/SSH/Modal)."""
    if terminal_env is None:
        # Fall back to native when no terminal env is attached
        return True
    env_type = type(terminal_env).__name__.lower()
    # Non-local backends
    for marker in ("docker", "ssh", "modal", "singularity", "daytona"):
        if marker in env_type:
            return False
    return True


def _config_strategy() -> str:
    """Read the ``file_backend`` config key. Returns 'auto', 'native', 'mcp', or 'shell'."""
    try:
        from her_cli.config import load_config
        cfg = load_config()
        strategy = cfg.get("file_backend", "auto")
        if isinstance(strategy, str) and strategy in ("auto", "native", "mcp", "shell"):
            return strategy
    except Exception:
        pass
    return "auto"


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve_file_operations(
    terminal_env=None,
    cwd: Optional[str] = None,
) -> FileOperations:
    """Return the best FileOperations backend for the current environment.

    Args:
        terminal_env: The active terminal environment (ShellFileOperations needs
                      this for shell-based execution). Pass None when no terminal
                      is available (e.g. during agent tool init).
        cwd: Working directory. Falls back to terminal_env.cwd or os.getcwd().

    Returns:
        A FileOperations instance.
    """
    strategy = _config_strategy()
    cwd = cwd or getattr(terminal_env, "cwd", None) or os.getcwd()

    # Strategy → implementation mapping
    if strategy == "shell":
        return _build_shell(terminal_env, cwd)

    if strategy == "native":
        impl = _try_native(cwd)
        if impl is None:
            logger.warning("native backend requested but unavailable; falling back to shell")
            return _build_shell(terminal_env, cwd)
        return impl

    if strategy == "mcp":
        impl = _try_mcp(cwd)
        if impl is None:
            logger.warning("MCP backend requested but filesystem server not connected; falling back to shell")
            return _build_shell(terminal_env, cwd)
        return impl

    # "auto" — try best available
    if _mcp_backend_available():
        logger.debug("resolver: using MCP backend")
        impl = _try_mcp(cwd)
        if impl is not None:
            return impl

    if _is_local_backend(terminal_env):
        logger.debug("resolver: using Python native backend")
        impl = _try_native(cwd)
        if impl is not None:
            return impl

    logger.debug("resolver: using shell backend")
    return _build_shell(terminal_env, cwd)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _try_native(cwd: str) -> Optional[FileOperations]:
    """Try to build a PythonNativeFileOperations instance."""
    try:
        from .python_native import PythonNativeFileOperations
        return PythonNativeFileOperations(cwd=cwd)
    except Exception as e:
        logger.debug("native backend init failed: %s", e)
        return None


def _try_mcp(cwd: str) -> Optional[FileOperations]:
    """Try to build an McpFileOperations instance."""
    try:
        from .mcp import McpFileOperations
        return McpFileOperations(cwd=cwd)
    except Exception as e:
        logger.debug("MCP backend init failed: %s", e)
        return None


def _build_shell(terminal_env, cwd: str) -> ShellFileOperations:
    """Build a ShellFileOperations (always works with any terminal env)."""
    return ShellFileOperations(terminal_env, cwd=cwd)
