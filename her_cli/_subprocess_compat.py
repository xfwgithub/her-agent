"""POSIX subprocess helpers.

her is developed on Linux / macOS. All helpers assume POSIX semantics.
"""

from __future__ import annotations

import shutil
import sys
from typing import Sequence

__all__ = [
    "IS_WINDOWS",
    "resolve_node_command",
    "windows_detach_flags",
    "windows_hide_flags",
    "windows_detach_popen_kwargs",
]


IS_WINDOWS = False


# -----------------------------------------------------------------------------
# Node ecosystem launcher resolution
# -----------------------------------------------------------------------------


def resolve_node_command(name: str, argv: Sequence[str]) -> list[str]:
    """Resolve a Node-ecosystem command name to an absolute-path argv.

    ``shutil.which`` returns a fully-qualified path when found.
    That's a small change from bare-name resolution (the OS does
    its own PATH search) but functionally identical and has the side
    benefit of making the argv reproducible in logs.

    Behavior when the command is not on PATH:
    - Return the bare name — caller can still try other approaches,
      OR the subsequent Popen will raise FileNotFoundError.

    Args:
        name: The command name to resolve (``npm``, ``npx``, ``node`` …).
        argv: The remaining arguments.  Must NOT include ``name`` itself —
            this function builds the full argv list.

    Returns:
        A list suitable for passing to subprocess.Popen/run/call.
    """
    resolved = shutil.which(name)
    if resolved:
        return [resolved, *argv]
    return [name, *argv]


# -----------------------------------------------------------------------------
# Detached / hidden process creation
# -----------------------------------------------------------------------------


def windows_detach_flags() -> int:
    """Return 0 on POSIX (no-op).

    Removed in the Windows-free build.  Kept as a stub so call sites
    don't need conditional imports.
    """
    return 0


def windows_hide_flags() -> int:
    """Return 0 on POSIX (no-op).

    Removed in the Windows-free build.  Kept as a stub so call sites
    don't need conditional imports.
    """
    return 0


def windows_detach_popen_kwargs() -> dict:
    """Return ``{"start_new_session": True}`` — POSIX-equivalent detach.

    Usage pattern:

    .. code-block:: python

        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            **windows_detach_popen_kwargs(),
        )
    """
    return {"start_new_session": True}
