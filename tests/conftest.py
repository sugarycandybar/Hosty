"""Pytest fixtures for isolated Hosty tests.

All tests use a temporary HOSTY_DATA_DIR so they never touch the real
~/.local/share/hosty directory. The constants module is reloaded after
patching env so SERVERS_DIR / CACHE_DIR etc. point at the tmpdir.
"""

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_hosty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide an isolated Hosty data dir and reload constants-bound modules."""
    data_dir = tmp_path / "hosty-data"
    data_dir.mkdir(parents=True)

    monkeypatch.setenv("HOSTY_DATA_DIR", str(data_dir))

    # Reimport constants so DATA_DIR / SERVERS_DIR etc. pick up the new env
    import hosty.shared.utils.constants as constants_mod

    importlib.reload(constants_mod)

    # Also reload modules that captured constants at import time
    import hosty.shared.backend.download_manager as dm_mod
    import hosty.shared.backend.server_manager as sm_mod

    importlib.reload(dm_mod)
    importlib.reload(sm_mod)

    # Ensure dirs exist
    constants_mod.ensure_data_dirs()

    yield data_dir

    # Cleanup: restore original constants (reload again without env)
    monkeypatch.delenv("HOSTY_DATA_DIR", raising=False)
    importlib.reload(constants_mod)
    importlib.reload(dm_mod)
    importlib.reload(sm_mod)


@pytest.fixture()
def server_manager(tmp_hosty_dir: Path):
    """Return a fresh ServerManager backed by tmp_hosty_dir."""
    # Import here so constants have already been reloaded
    from hosty.shared.backend.server_manager import ServerManager

    mgr = ServerManager()
    # Start clean - no servers from previous runs
    assert len(mgr.servers) == 0
    return mgr
