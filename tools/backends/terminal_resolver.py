"""Terminal backend resolver.

Follows the same pattern as ``tools/backends/resolver.py`` for file operations:
select the best terminal backend at runtime based on configuration.

Terminal backends already exist in ``tools/environments/``:

  - ``local`` — direct shell (fastest, available everywhere)
  - ``docker`` — containerized via Docker
  - ``ssh`` — remote SSH host
  - ``modal`` — Modal.com serverless
  - ``singularity`` — Singularity containers
  - ``daytona`` — Daytona workspaces

Config key: ``terminal_backend`` (in ``~/.her/config.yaml``)

  ``"auto"`` (default)
    Uses the existing ``TERMINAL_ENV`` env var or ``terminal.env_type`` config.

  ``"local"``, ``"docker"``, etc.
    Pins a specific backend.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _config_backend() -> str:
    """Read the ``terminal_backend`` config key."""
    try:
        from her_cli.config import load_config
        cfg = load_config()
        strategy = cfg.get("terminal_backend", "auto")
        if isinstance(strategy, str):
            return strategy
    except Exception:
        pass
    return "auto"


def resolve_terminal_config(overrides: dict | None = None) -> dict:
    """Resolve terminal environment configuration.

    Returns a dict with keys: env_type, image, cwd, timeout, and optional
    ssh_config / container_config / local_config.

    ``overrides`` can override any of the resolved values (used by
    ``_get_file_ops`` in file_tools.py for per-task overrides).
    """
    from tools.terminal_tool import _get_env_config

    cfg = _get_env_config()

    # Apply backend override
    backend = _config_backend()
    if backend != "auto":
        cfg["env_type"] = backend

    if overrides:
        cfg.update(overrides)

    return cfg


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
):
    """Create the appropriate terminal environment.

    Thin wrapper around ``terminal_tool._create_environment`` that can be
    extended to support new backends (e.g. MCP-based terminal).

    Falls back to the shell backend when the requested backend is unavailable.
    """
    from tools.terminal_tool import _create_environment

    return _create_environment(
        env_type=env_type,
        image=image,
        cwd=cwd,
        timeout=timeout,
        ssh_config=ssh_config,
        container_config=container_config,
        local_config=local_config,
        task_id=task_id,
        host_cwd=host_cwd,
    )
