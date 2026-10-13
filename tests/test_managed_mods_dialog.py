"""Tests for the managed-mods dialog metadata merge (no display)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from hosty.gtk_ui.dialogs.managed_mods import server_mod_metadata  # noqa: E402


class _StubManager:
    def __init__(self, individual, packs):
        self._individual = individual
        self._packs = packs

    def mod_file_metadata(self, server_id):
        return dict(self._individual)

    def modpack_file_details(self, server_id, project_id):
        return dict(self._packs.get(project_id, {}))


def test_pack_details_win_over_individual():
    mgr = _StubManager(
        {"a.jar": {"title": "Ind", "version": "1", "project_id": "x"}},
        {"pack": {"a.jar": {"title": "Pack", "version": "2", "project_id": "y"}}},
    )
    merged = server_mod_metadata(mgr, "s1", "pack")
    assert merged["a.jar"] == {"title": "Pack", "version": "2", "project_id": "y"}


def test_individual_only_without_pack():
    mgr = _StubManager(
        {"a.jar": {"title": "Ind", "version": "1", "project_id": "x"}},
        {},
    )
    merged = server_mod_metadata(mgr, "s1", "pack")
    assert merged["a.jar"]["title"] == "Ind"


def test_unavailable_manager_is_empty():
    assert server_mod_metadata(None, "s1", "pack") == {}
    assert server_mod_metadata(_StubManager({}, {}), "s1", None) == {}
