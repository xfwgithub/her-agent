from unittest.mock import patch


def test_service_path_skips_nonexistent_node_modules(tmp_path):
    """Service PATH should not include node_modules/.bin if it doesn't exist."""
    from her_cli.gateway import _build_service_path_dirs
    with patch("her_cli.gateway.get_her_home", return_value=tmp_path / ".her"):
        dirs = _build_service_path_dirs(project_root=tmp_path)
    node_modules_bin = str(tmp_path / "node_modules" / ".bin")
    assert node_modules_bin not in dirs


def test_service_path_includes_node_modules_when_present(tmp_path):
    """Service PATH should include node_modules/.bin when it exists."""
    nm_bin = tmp_path / "node_modules" / ".bin"
    nm_bin.mkdir(parents=True)
    from her_cli.gateway import _build_service_path_dirs
    with patch("her_cli.gateway.get_her_home", return_value=tmp_path / ".her"):
        dirs = _build_service_path_dirs(project_root=tmp_path)
    assert str(nm_bin) in dirs


def test_service_path_includes_her_home_node_modules(tmp_path):
    """Service PATH should include ~/.her/node_modules/.bin when it exists."""
    her_nm = tmp_path / ".her" / "node_modules" / ".bin"
    her_nm.mkdir(parents=True)
    from her_cli.gateway import _build_service_path_dirs
    with patch("her_cli.gateway.get_her_home", return_value=tmp_path / ".her"):
        dirs = _build_service_path_dirs(project_root=tmp_path)
    assert str(her_nm) in dirs
