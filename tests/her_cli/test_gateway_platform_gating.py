"""Host-specific gating in ``her_cli.gateway._all_platforms()``."""

class TestMatrixPlatformGating:
    def test_matrix_present_on_linux(self, monkeypatch):
        """Sanity: matrix is still in the picker on Linux/macOS."""
        import her_cli.gateway as gateway_mod

        monkeypatch.setattr(gateway_mod.sys, "platform", "linux")
        platforms = gateway_mod._all_platforms()
        keys = {p["key"] for p in platforms}
        assert "matrix" in keys, "matrix must be available on Linux"

    def test_matrix_present_on_macos(self, monkeypatch):
        import her_cli.gateway as gateway_mod

        monkeypatch.setattr(gateway_mod.sys, "platform", "darwin")
        platforms = gateway_mod._all_platforms()
        keys = {p["key"] for p in platforms}
        assert "matrix" in keys, "matrix must be available on macOS"


