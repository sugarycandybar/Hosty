"""Tests for sidebar server ordering and backup retention settings."""

import json
import os
import time
from pathlib import Path

from hosty.shared.backend.preferences_manager import PreferencesManager


def _ids(mgr):
    return [s.id for s in mgr.servers]


def test_add_server_appends_last(server_manager):
    a = server_manager.add_server(name="A", mc_version="1.21.4")
    b = server_manager.add_server(name="B", mc_version="1.21.4")
    c = server_manager.add_server(name="C", mc_version="1.21.4")
    assert _ids(server_manager) == [a.id, b.id, c.id]


def test_move_up_down(server_manager):
    a = server_manager.add_server(name="A", mc_version="1.21.4")
    b = server_manager.add_server(name="B", mc_version="1.21.4")
    c = server_manager.add_server(name="C", mc_version="1.21.4")

    assert server_manager.move_server_up(b.id) is True
    assert _ids(server_manager) == [b.id, a.id, c.id]

    assert server_manager.move_server_down(b.id) is True
    assert _ids(server_manager) == [a.id, b.id, c.id]

    # Ends are no-ops
    assert server_manager.move_server_up(a.id) is False
    assert _ids(server_manager) == [a.id, b.id, c.id]
    assert server_manager.move_server_down(c.id) is False
    assert _ids(server_manager) == [a.id, b.id, c.id]

    assert server_manager.move_server("nope", -1) is False
    assert server_manager.move_server(a.id, 0) is False


def test_move_normalizes_legacy_positions(server_manager):
    a = server_manager.add_server(name="A", mc_version="1.21.4")
    b = server_manager.add_server(name="B", mc_version="1.21.4")
    # Simulate pre-ordering saves where every server has position 0
    for info in server_manager.servers:
        info.position = 0
    assert server_manager.move_server_down(a.id) is True
    assert _ids(server_manager) == [b.id, a.id]
    positions = [s.position for s in server_manager.servers]
    assert positions == sorted(positions)


def test_move_emits_reordered(server_manager):
    a = server_manager.add_server(name="A", mc_version="1.21.4")
    server_manager.add_server(name="B", mc_version="1.21.4")
    seen = []
    server_manager.connect("servers-reordered", lambda *_: seen.append(True))
    server_manager.move_server_down(a.id)
    assert seen == [True]


def test_position_survives_reload(server_manager, tmp_hosty_dir):
    a = server_manager.add_server(name="A", mc_version="1.21.4")
    b = server_manager.add_server(name="B", mc_version="1.21.4")
    server_manager.move_server_up(b.id)
    assert _ids(server_manager) == [b.id, a.id]

    from hosty.shared.backend.server_manager import ServerManager

    fresh = ServerManager()
    assert [s.id for s in fresh.servers] == [b.id, a.id]


# ---------------------------------------------------------------------------
# Backup retention preferences
# ---------------------------------------------------------------------------


def test_retention_defaults(tmp_path):
    prefs = PreferencesManager(tmp_path / "settings.json")
    assert prefs.backup_retention_days == 30
    assert prefs.max_backups == 0


def test_retention_migrates_legacy_toggle(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auto_delete_old_backups": False}))
    assert PreferencesManager(path).backup_retention_days == 0

    path.write_text(json.dumps({"auto_delete_old_backups": True}))
    assert PreferencesManager(path).backup_retention_days == 30

    # Explicit new key wins over the legacy toggle
    path.write_text(json.dumps({"auto_delete_old_backups": False, "backup_retention_days": 14}))
    assert PreferencesManager(path).backup_retention_days == 14


def test_retention_setters_validate(tmp_path):
    prefs = PreferencesManager(tmp_path / "settings.json")
    prefs.backup_retention_days = 7
    assert prefs.backup_retention_days == 7
    prefs.backup_retention_days = -5
    assert prefs.backup_retention_days == 0
    prefs.max_backups = 10
    assert prefs.max_backups == 10
    prefs.max_backups = -3
    assert prefs.max_backups == 0
    prefs.max_backups = 10**9
    assert prefs.max_backups == 1000


def _make_backup(backups_dir: Path, name: str, age_days: float) -> Path:
    target = backups_dir / name
    target.write_bytes(b"fake-zip")
    stamp = time.time() - age_days * 86400
    os.utime(target, (stamp, stamp))
    return target


def test_cleanup_by_age(server_manager):
    info = server_manager.add_server(name="A", mc_version="1.21.4")
    backups = Path(info.server_dir) / "hosty-backups"
    backups.mkdir(parents=True)
    old = _make_backup(backups, "old.zip", age_days=10)
    new = _make_backup(backups, "new.zip", age_days=1)
    (backups / "notes.txt").write_text("not a zip")

    server_manager.preferences.backup_retention_days = 7
    server_manager.preferences.max_backups = 0
    server_manager._cleanup_old_backups(info.id)

    assert not old.exists()
    assert new.exists()
    assert (backups / "notes.txt").exists()


def test_cleanup_disabled_keeps_everything(server_manager):
    info = server_manager.add_server(name="A", mc_version="1.21.4")
    backups = Path(info.server_dir) / "hosty-backups"
    backups.mkdir(parents=True)
    old = _make_backup(backups, "old.zip", age_days=365)

    server_manager.preferences.backup_retention_days = 0
    server_manager.preferences.max_backups = 0
    server_manager._cleanup_old_backups(info.id)

    assert old.exists()


def test_cleanup_trims_to_max_count_newest_kept(server_manager):
    info = server_manager.add_server(name="A", mc_version="1.21.4")
    backups = Path(info.server_dir) / "hosty-backups"
    backups.mkdir(parents=True)
    _make_backup(backups, "oldest.zip", age_days=5)
    _make_backup(backups, "middle.zip", age_days=3)
    _make_backup(backups, "newest.zip", age_days=1)

    server_manager.preferences.backup_retention_days = 0
    server_manager.preferences.max_backups = 2
    server_manager._cleanup_old_backups(info.id)

    remaining = sorted(p.name for p in backups.glob("*.zip"))
    assert remaining == ["middle.zip", "newest.zip"]
