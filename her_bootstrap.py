"""UTF-8 stdio bootstrap — preserved as a no-op for POSIX-only environments.

This module was historically responsible for configuring UTF-8 stdio on
Windows. On POSIX the module is a no-op — POSIX systems are already UTF-8
by default in 99% of cases.  The import is kept at the top of entry points
so code can safely ``import her_bootstrap`` regardless of platform.
"""

from __future__ import annotations


def apply_windows_utf8_bootstrap() -> bool:
    """No-op on POSIX. Returns False."""
    return False


# Apply on import — keeping the side-effect convention.
apply_windows_utf8_bootstrap()
