"""Regression tests for _apply_profile_override HER_HOME guard (issue #22502).

When HER_HOME is set to the her root (e.g. systemd hardcodes
HER_HOME=/root/.her), _apply_profile_override must still read
active_profile and update HER_HOME to the profile directory.

When HER_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path



def _run_apply_profile_override(
    tmp_path, monkeypatch, *, her_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["HER_HOME"] after the call,
    or None if unset.
    """
    her_root = tmp_path / ".her"
    her_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (her_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (her_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if her_home is not None:
        monkeypatch.setenv("HER_HOME", her_home)
    else:
        monkeypatch.delenv("HER_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["her", "gateway", "start"])

    from her_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("HER_HOME")


class TestApplyProfileOverrideherHomeGuard:
    """Regression guard for issue #22502.

    Verifies that HER_HOME pointing to the her root does NOT suppress
    the active_profile check, while HER_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_her_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """HER_HOME=/root/.her + active_profile=coder must redirect
        HER_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets HER_HOME to the her root
        and the user switches to a profile via `her profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        her_root = tmp_path / ".her"
        her_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            her_home=str(her_root),
            active_profile="coder",
        )

        assert result is not None, "HER_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected HER_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected HER_HOME to end with 'coder', got: {result!r}"
        )

    def test_her_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """HER_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with HER_HOME already set to a specific profile must stay in that
        profile.
        """
        her_root = tmp_path / ".her"
        profile_dir = her_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (her_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HER_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["her", "gateway", "start"])

        from her_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HER_HOME") == str(profile_dir), (
            "HER_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_her_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: HER_HOME unset + active_profile=coder must set
        HER_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            her_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_her_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect HER_HOME."""
        her_root = tmp_path / ".her"
        her_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HER_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["her", "gateway", "start"])
        (her_root / "active_profile").write_text("default")

        from her_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HER_HOME") is None
