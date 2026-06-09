"""Terminal backend resolver.

Follows the same pattern as ``tools/backends/resolver.py`` for file operations:
selects the best terminal backend at runtime based on configuration.

Config key: ``terminal_backend`` (in ``~/.her/config.yaml``)

  ``"auto"`` (default)
    Uses the existing ``TERMINAL_ENV`` env var or config (no override).

  ``"local"``, ``"docker"``, ``"ssh"``, ``"modal"``, ``"singularity"``, ``"daytona"``
    Pins a specific backend from ``tools/environments/``.

Only backends that actually exist in the codebase are supported.
See ``tools/environments/`` for the full list.
"""

from __future__ import annotations

import logging
from typing import Optional

from tools.environments.base import BaseEnvironment

logger = logging.getLogger(__name__)

# Valid backends that actually exist in tools/environments/
_REAL_BACKENDS = {"local", "docker", "ssh", "modal", "singularity", "daytona"}


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

    # "auto" → use whatever env_type the caller already resolved
    if backend == "auto":
        effective_type = env_type
    elif backend in _REAL_BACKENDS:
        effective_type = backend
    else:
        logger.warning("Unknown terminal_backend %r; falling back to %r", backend, env_type)
        effective_type = env_type

    return _create_environment(
        env_type=effective_type,
        image=image, cwd=cwd, timeout=timeout,
        ssh_config=ssh_config, container_config=container_config,
        local_config=local_config, task_id=task_id, host_cwd=host_cwd,
    )
