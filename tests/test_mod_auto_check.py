"""Tests for the silent mod auto-check (MC-version-row style status).

The auto-check reuses the manual check machinery in silent mode (no
dialog presented) and only updates the Check-for-updates row subtitle.
"""

import time
import types

from gi.repository import GLib

from hosty.gtk_ui.views.files.mixins.mods_mixin import ModsMixin


class _FakeRow:
    def __init__(self):
        self.subtitles = []

    def set_subtitle(self, text):
        self.subtitles.append(str(text))


def _fake_host(row=None):
    host = types.SimpleNamespace()
    host._mods_update_busy = False
    host._mods_auto_check_busy = False
    host._is_running = lambda: False
    host._server_info = types.SimpleNamespace(id="s1", mc_version="1.20.1")
    host._server_manager = None
    host._server_loader = lambda: "fabric"
    host._modpack_entries = lambda: {}
    host._read_individual_mod_state = lambda: {"mods": {}}
    host._read_datapack_state = lambda: {"datapacks": {}}
    host._read_plugin_state = lambda: {"plugins": {}}
    host._modpack_managed_mod_map = lambda: {}
    host._rebuild_lists = lambda: None
    host._check_updates_row = row
    host._toast = lambda *a: None
    host._alert = lambda *a: None
    host._on_check_mod_updates = types.MethodType(ModsMixin._on_check_mod_updates, host)
    host._refresh_updates_row = types.MethodType(ModsMixin._refresh_updates_row, host)
    host._auto_check_mod_updates = types.MethodType(ModsMixin._auto_check_mod_updates, host)
    return host


def _pump_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_refresh_updates_row_states():
    host = _fake_host(_FakeRow())
    ModsMixin._refresh_updates_row(host, 0)
    ModsMixin._refresh_updates_row(host, 1)
    ModsMixin._refresh_updates_row(host, 3)
    assert host._check_updates_row.subtitles[0] == "Up to date"
    assert host._check_updates_row.subtitles[1] == "1 update available"
    assert host._check_updates_row.subtitles[2] == "3 updates available"


def test_refresh_updates_row_missing_is_safe():
    ModsMixin._refresh_updates_row(_fake_host(None), 5)


def test_silent_check_updates_row_and_resets():
    row = _FakeRow()
    host = _fake_host(row)
    ModsMixin._auto_check_mod_updates(host)
    assert row.subtitles == ["Checking for updates..."]
    assert _pump_until(lambda: not host._mods_auto_check_busy)
    assert row.subtitles[-1] == "Up to date"
    assert host._mods_update_busy is False


def test_silent_check_skipped_when_busy_or_running():
    row = _FakeRow()
    host = _fake_host(row)
    host._mods_update_busy = True
    ModsMixin._auto_check_mod_updates(host)
    assert row.subtitles == []
    host._mods_update_busy = False
    host._is_running = lambda: True
    ModsMixin._auto_check_mod_updates(host)
    assert row.subtitles == []


def test_silent_check_ignores_stale_server():
    row = _FakeRow()
    host = _fake_host(row)
    ModsMixin._auto_check_mod_updates(host)
    host._server_info = types.SimpleNamespace(id="s2", mc_version="1.20.1")
    assert _pump_until(lambda: not host._mods_auto_check_busy)
    # Result belonged to s1: row keeps only the transient state.
    assert row.subtitles == ["Checking for updates..."]
