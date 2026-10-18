"""Paper (Bukkit plugin) server support: content types, rows, and wording.

Paper has no ``mods/`` directory and no modpacks: the Modrinth page must
offer Plugins instead of Mods, installed plugin jars must be listed with
their plugin metadata, and loader-facing labels must say "plugin".
"""

from __future__ import annotations

import builtins
import json
import types
import urllib.parse

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


def _ensure_gettext():
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s  # noqa: E731
    if not hasattr(builtins, "ngettext"):
        builtins.ngettext = lambda singular, plural, n: singular if n == 1 else plural  # noqa: E731


# ---------------------------------------------------------------------------
# Modrinth content-type filter
# ---------------------------------------------------------------------------


def test_project_type_items_for_mod_loaders():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin

    expected = [("Mods", "mod"), ("Modpacks", "modpack"), ("Datapacks", "datapack")]
    assert ModrinthMixin._project_type_items("fabric") == expected
    assert ModrinthMixin._project_type_items("neoforge") == expected
    assert ModrinthMixin._project_type_items("forge") == expected
    assert ModrinthMixin._project_type_items(None) == expected


def test_project_type_items_for_paper():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin

    items = ModrinthMixin._project_type_items("paper")
    assert items == [("Plugins", "plugin"), ("Datapacks", "datapack")]
    # Plugins must be the default selection (first entry), and Paper must
    # never offer mods or modpacks.
    assert items[0][1] == "plugin"
    assert all(value not in ("mod", "modpack") for _label, value in items)


def test_project_type_items_for_arclight_keeps_hybrid_order():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin

    assert ModrinthMixin._project_type_items("arclight") == [
        ("Mods", "mod"),
        ("Plugins", "plugin"),
        ("Modpacks", "modpack"),
        ("Datapacks", "datapack"),
    ]


# ---------------------------------------------------------------------------
# Install alerts
# ---------------------------------------------------------------------------


def test_stop_before_install_message_matches_content_type():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import _stop_before_install_message

    assert _stop_before_install_message(True) == "Stop the server before installing plugins."
    assert _stop_before_install_message(False) == "Stop the server before installing mods."


def test_select_before_install_message_matches_content_type():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import _select_before_install_message

    assert _select_before_install_message(True) == "Select a server before installing plugins."
    assert _select_before_install_message(False) == "Select a server before installing mods."


# ---------------------------------------------------------------------------
# Category filter actually filters plugin/datapack searches
# ---------------------------------------------------------------------------


@pytest.fixture()
def search_urls(monkeypatch: pytest.MonkeyPatch):
    from hosty.shared.backend import modrinth_client

    urls: list[str] = []

    def fake_request_json(url, timeout=30.0, cache_ttl=0.0):
        urls.append(url)
        return {"hits": [], "total_hits": 0}

    monkeypatch.setattr(modrinth_client, "_request_json", fake_request_json)
    modrinth_client.clear_cache()
    yield urls
    modrinth_client.clear_cache()


def _facets(url: str) -> list[list[str]]:
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return json.loads(params["facets"][0])


def test_category_facet_is_applied_to_plugin_searches(search_urls):
    from hosty.shared.backend import modrinth_client

    modrinth_client.search_mods("worldedit", project_type="plugin", category="utility")
    assert ["categories:utility"] in _facets(search_urls[-1])


def test_category_facet_is_applied_to_datapack_searches(search_urls):
    from hosty.shared.backend import modrinth_client

    modrinth_client.search_mods("", project_type="datapack", category="worldgen")
    facets = _facets(search_urls[-1])
    assert ["categories:worldgen"] in facets
    assert ["project_type:datapack"] in facets


def test_plugin_search_without_category_has_no_category_facet(search_urls):
    from hosty.shared.backend import modrinth_client

    modrinth_client.search_mods("worldedit", project_type="plugin")
    facets = _facets(search_urls[-1])
    assert not any(facet[0].startswith("categories:") for facet in facets)


def test_mod_search_keeps_loader_and_category_facets(search_urls):
    from hosty.shared.backend import modrinth_client

    modrinth_client.search_mods("sodium", project_type="mod", loader="fabric", category="optimization")
    facets = _facets(search_urls[-1])
    assert ["categories:fabric"] in facets
    assert ["categories:optimization"] in facets


# ---------------------------------------------------------------------------
# Installed plugin rows (shared by Arclight and Paper lists)
# ---------------------------------------------------------------------------


def _plugin_row_stub(state: dict):
    from hosty.gtk_ui.views.files.main_view import FilesView
    from hosty.gtk_ui.views.files.mixins.mods_mixin import ModsMixin

    stub = types.SimpleNamespace()
    stub._read_plugin_state = lambda: {"plugins": state}
    stub._icon_button = types.MethodType(FilesView._icon_button, stub)
    stub._make_plugin_row = types.MethodType(ModsMixin._make_plugin_row, stub)
    stub._make_plugin_row_from_meta = types.MethodType(ModsMixin._make_plugin_row_from_meta, stub)
    stub._confirm_delete_plugin = lambda *a, **k: None
    stub._forget_plugin_record = lambda *a, **k: None
    return stub


def test_populate_plugin_rows_uses_plugin_metadata(tmp_path):
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.mods_mixin import ModsMixin

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "tracked-1.0.jar").write_bytes(b"jar")
    (plugins_dir / "manual.jar").write_bytes(b"jar")

    state = {
        "proj-1": {
            "title": "WorldEdit",
            "filename": "tracked-1.0.jar",
            "version_id": "ver-1",
            "version_number": "7.3.0",
        }
    }
    stub = _plugin_row_stub(state)
    stub._populate_plugin_rows = types.MethodType(ModsMixin._populate_plugin_rows, stub)

    expander = Adw.ExpanderRow()
    rows: list[Gtk.Widget] = []
    count = stub._populate_plugin_rows(expander, plugins_dir, rows)

    assert count == 2
    assert len(rows) == 2
    titles = [row.get_title() for row in rows]
    # Tracked plugin: friendly title + version from plugin state, not the jar name.
    assert "WorldEdit" in titles
    assert "manual.jar" in titles
    tracked = next(row for row in rows if row.get_title() == "WorldEdit")
    assert "7.3.0" in tracked.get_subtitle()


def test_populate_plugin_rows_flags_missing_tracked_files(tmp_path):
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.mods_mixin import ModsMixin

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    stub = _plugin_row_stub({"proj-2": {"title": "Gone", "filename": "gone.jar", "version_number": "1.0"}})
    stub._populate_plugin_rows = types.MethodType(ModsMixin._populate_plugin_rows, stub)

    rows: list[Gtk.Widget] = []
    assert stub._populate_plugin_rows(Adw.ExpanderRow(), plugins_dir, rows) == 1
    assert rows[0].get_title() == "Gone"
    assert rows[0].get_subtitle() == "File missing"


def test_plugin_row_offers_plugin_page_and_delete(tmp_path):
    _ensure_gettext()

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    jar = plugins_dir / "tracked-1.0.jar"
    jar.write_bytes(b"jar")

    stub = _plugin_row_stub(
        {"proj-1": {"title": "WorldEdit", "filename": "tracked-1.0.jar", "version_number": "7.3.0"}}
    )
    calls: list[tuple[str, str]] = []

    def fake_icon_button(icon_name, tooltip, *_a, **_k):
        calls.append((icon_name, tooltip))
        return Gtk.Button()

    stub._icon_button = fake_icon_button
    stub._make_plugin_row(jar)

    assert ("web-browser-symbolic", "Open plugin page") in calls
    assert ("user-trash-symbolic", "Delete plugin") in calls


# ---------------------------------------------------------------------------
# Server detail wording
# ---------------------------------------------------------------------------


def test_content_is_plugins_only_for_plugin_loaders():
    _ensure_gettext()
    from hosty.gtk_ui.views.server_detail import ServerDetailView

    stub = types.SimpleNamespace(_current_server=types.SimpleNamespace(loader_type="paper"))
    assert ServerDetailView._content_is_plugins(stub) is True
    # Arclight runs mods alongside plugins, so wording stays "Mods".
    stub._current_server = types.SimpleNamespace(loader_type="arclight")
    assert ServerDetailView._content_is_plugins(stub) is False
    stub._current_server = types.SimpleNamespace(loader_type="fabric")
    assert ServerDetailView._content_is_plugins(stub) is False
    stub._current_server = None
    assert ServerDetailView._content_is_plugins(stub) is False


# ---------------------------------------------------------------------------
# Files view labels
# ---------------------------------------------------------------------------


def _titles_stub(loader: str):
    from hosty.gtk_ui.views.files.main_view import FilesView

    stub = types.SimpleNamespace()
    stub._server_info = types.SimpleNamespace(loader_type=loader)
    stub._mods_group = Adw.PreferencesGroup()
    stub._open_mods_row = Adw.ActionRow()
    stub._open_plugins_row = Adw.ActionRow()
    stub._check_updates_row = Adw.ActionRow()
    stub._plugins_expander = Adw.ExpanderRow()
    stub._mods_expander = Adw.ExpanderRow()
    stub._mod_rows = []
    stub._is_paper = types.MethodType(FilesView._is_paper, stub)
    stub._is_arclight = types.MethodType(FilesView._is_arclight, stub)
    stub._update_mods_titles = types.MethodType(FilesView._update_mods_titles, stub)
    return stub


def test_paper_titles_say_plugins():
    _ensure_gettext()
    stub = _titles_stub("paper")
    stub._update_mods_titles()
    assert stub._mods_group.get_title() == "Plugins"
    assert stub._mods_expander.get_title() == "Installed Plugins"
    assert stub._open_mods_row.get_title() == "Open plugins folder"
    assert stub._check_updates_row.get_title() == "Plugin updates"
    # Arclight-only plugin section stays hidden on Paper.
    assert stub._plugins_expander.get_visible() is False


def test_mod_loader_titles_unchanged():
    _ensure_gettext()
    stub = _titles_stub("fabric")
    stub._update_mods_titles()
    assert stub._mods_group.get_title() == "Mods"
    assert stub._mods_expander.get_title() == "Installed Mods"
    assert stub._open_mods_row.get_title() == "Open mods folder"
    assert stub._check_updates_row.get_title() == "Mod updates"


def test_arclight_titles_keep_hybrid_labels():
    _ensure_gettext()
    stub = _titles_stub("arclight")
    stub._update_mods_titles()
    # Group titles are Pango markup, so "&" arrives escaped.
    assert stub._mods_group.get_title() == "Mods &amp; Plugins"
    assert stub._plugins_expander.get_visible() is True
