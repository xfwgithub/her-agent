"""Terminal backend resolver.

Follows the same pattern as ``tools/backends/resolver.py`` for file operations:
selects the best terminal backend at runtime based on configuration.

Config key: ``terminal_backend`` (in ``~/.her/config.yaml``)

  ``"auto"`` (default)
    Uses the existing ``TERMINAL_ENV`` env var or config. If a shell-capable
    MCP server is connected, prefers that.

  ``"local"``, ``"docker"``, ``"ssh"``, ``"modal"``, etc.
    Pins a specific backend from ``tools/environments/``.

  ``"mcp"``
    Uses MCP shell server (requires an MCP server with shell capability).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from tools.environments.base import BaseEnvironment

logger = logging.getLogger(__name__)


def _config_backend() -> str:
    """Read the ``terminal_backend`` config key. Default: ``auto``."""
    try:
        from her_cli.config import load_config
        cfg = load_config()
        strategy = cfg.get("terminal_backend", "auto")
        if isinstance(strategy, str):
            return strategy
    except Exception:
        pass
    return "auto"


def _mcp_shell_available() -> bool:
    """Check if any connected MCP server provides shell execution."""
    try:
        from tools.backends.mcp_terminal import _find_shell_server
        return _find_shell_server() is not None
    except Exception:
        return False


def resolve_terminal_env(
    env_type: str,
    image: str,
    cwd: str,
    timeout: int,
    ssh_config: dict | None = None,
    container_config: dict | None = None,
    local_config: dict | None = None,
    task_id: str = "default",
    host_cwd: str | None = None,
) -> BaseEnvironment:
    """Create the appropriate terminal environment.

    ``env_type`` is the caller's requested type (from ``_get_env_config()``).
    The resolver may override it based on ``terminal_backend`` config.
    """
    from tools.terminal_tool import _create_environment

    backend = _config_backend()

    # Resolve effective env_type
    if backend == "mcp":
        if _mcp_shell_available():
            from .mcp_terminal import McpTerminalBackend
            return McpTerminalBackend(cwd=cwd)
        logger.warning("MCP terminal requested but no shell-capable server connected; falling back")
        effective_type = env_type
    elif backend != "auto":
        effective_type = backend
    else:
        # "auto": prefer MCP if available, else use the configured env_type
        if _mcp_shell_available():
            from .mcp_terminal import McpTerminalBackend
            return McpTerminalBackend(cwd=cwd)
        effective_type = env_type

    return _create_environment(
        env_type=effective_type,
        image=image, cwd=cwd, timeout=timeout,
        ssh_config=ssh_config, container_config=container_config,
        local_config=local_config, task_id=task_id, host_cwd=host_cwd,
    )
