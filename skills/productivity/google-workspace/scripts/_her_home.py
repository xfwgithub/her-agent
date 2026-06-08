"""Resolve HER_HOME for standalone skill scripts.

Skill scripts may run outside the her process (e.g. system Python,
nix env, CI) where ``her_constants`` is not importable.  This module
provides the same ``get_her_home()`` and ``display_her_home()``
contracts as ``her_constants`` without requiring it on ``sys.path``.

When ``her_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``her_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``HER_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from her_constants import display_her_home as display_her_home
    from her_constants import get_her_home as get_her_home
except (ModuleNotFoundError, ImportError):

    def get_her_home() -> Path:
        """Return the her home directory (default: ~/.her).

        Mirrors ``her_constants.get_her_home()``."""
        val = os.environ.get("HER_HOME", "").strip()
        return Path(val) if val else Path.home() / ".her"

    def display_her_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``her_constants.display_her_home()``."""
        home = get_her_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
