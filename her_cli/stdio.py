"""No-op stubs for the Windows-free build.

This module previously held Windows-safe stdio configuration.
All functions are no-ops on POSIX.
"""

from __future__ import annotations

__all__ = ["configure_windows_stdio", "is_windows"]


def is_windows() -> bool:
    """Return False — Windows is not supported."""
    return False


def configure_windows_stdio() -> bool:
    """No-op on POSIX.  Returns False."""
    return False
