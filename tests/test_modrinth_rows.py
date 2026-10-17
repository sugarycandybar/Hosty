"""Tests for Modrinth result-row performance work (no network, no display).

Covers: responsive-layout threshold semantics, shared icon fetching
(one download feeds both row layouts plus the memory cache), and
precomputed install-state snapshots (no per-row disk reads).
"""

import time
import types

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from hosty.gtk_ui.views.files.mixins import modrinth_mixin as mixin_mod  # noqa: E402
from hosty.shared.backend.modrinth_client import ModrinthHit  # noqa: E402


def _pump_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_row_mode_thresholds():
    fn = mixin_mod._modrinth_row_mode_for_width
    assert fn(0) == "compact"
    assert fn(-5) == "compact"
    assert fn(1) == "expanded"
    assert fn(549) == "expanded"
    assert fn(550) == "compact"
    assert fn(1920) == "compact"
    assert fn("bogus") == "compact"


def _write_png(path, color):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 32, 32)
    pixbuf.fill(color)
    pixbuf.savev(str(path), "png", [], [])


def test_icon_fetch_shared_and_cached(tmp_path, monkeypatch):
    from hosty.shared.backend import modrinth_client

    icon_path = tmp_path / "icon.png"
    _write_png(icon_path, 0xFF0000FF)
    calls = []

    def fake_get_icon_path(url, timeout=20.0):
        calls.append(url)
        return str(icon_path)

    monkeypatch.setattr(modrinth_client, "get_icon_path", fake_get_icon_path)

    host = types.SimpleNamespace()
    host._modrinth_icon_textures = {}
    host._load_icon_async = types.MethodType(mixin_mod.ModrinthMixin._load_icon_async, host)

    first_a, first_b = Gtk.Image(), Gtk.Image()
    host._load_icon_async([first_a, first_b], "https://cdn.example/a.png", size=24)
    assert _pump_until(lambda: first_a.get_paintable() is not None and first_b.get_paintable() is not None)
    assert calls == ["https://cdn.example/a.png"]

    # Same URL again: memory cache, no new fetch, instant paint.
    second_a, second_b = Gtk.Image(), Gtk.Image()
    host._load_icon_async([second_a, second_b], "https://cdn.example/a.png", size=24)
    assert _pump_until(lambda: second_a.get_paintable() is not None and second_b.get_paintable() is not None)
    assert calls == ["https://cdn.example/a.png"]


def _hit(pid="pid-1", title="Mod One", ptype="mod"):
    return ModrinthHit(
        project_id=pid,
        slug="mod-one",
        title=title,
        description="Does things",
        icon_url="",
        latest_version="",
        downloads=10,
        author="auth",
        categories=[],
        project_type=ptype,
    )


def _row_buttons(row):
    found = []

    def walk(widget):
        yield widget
        try:
            child = widget.get_first_child()
        except Exception:
            return
        while child is not None:
            yield from walk(child)
            try:
                child = child.get_next_sibling()
            except Exception:
                return

    for widget in walk(row):
        if isinstance(widget, Gtk.Button) and widget.get_label():
            found.append(widget)
    return found


def _raising(name):
    def _boom(*args, **kwargs):
        raise RuntimeError(f"must not touch disk: {name}")

    return _boom


def test_row_build_uses_snapshot_not_disk():
    host = types.SimpleNamespace()
    host._server_loader = lambda: "fabric"
    host._looks_installed = lambda hit, names: True
    host._is_modpack_installed = _raising("modpacks")
    host._is_datapack_installed = _raising("datapacks")
    host._is_plugin_installed = _raising("plugins")

    row = mixin_mod.ModrinthMixin._make_modrinth_row(
        host,
        _hit(pid="other"),
        "",
        set(),
        installed_packs={"pid-1"},
        installed_dps=set(),
        installed_plugins=set(),
    )
    buttons = _row_buttons(row)
    assert buttons, "expected install buttons"
    assert all(b.get_label() == "Installed" and not b.get_sensitive() for b in buttons)


def test_decide_uses_snapshot_not_disk():
    host = types.SimpleNamespace()
    host._is_modpack_installed = _raising("modpacks")
    host._is_datapack_installed = _raising("datapacks")
    host._is_plugin_installed = _raising("plugins")
    host._dependency_dependents = lambda filename: []
    host._is_mod_present = lambda pid, names: False
    host._looks_installed = lambda hit, names: False
    decide = mixin_mod.ModrinthMixin._decide_row_install_button

    label, sensitive = decide(
        host,
        _hit(),
        None,
        set(),
        is_modpack=True,
        is_datapack=False,
        is_plugin=False,
        btn_label="Install",
        installed_packs={"pid-1"},
        installed_dps=set(),
        installed_plugins=set(),
    )
    assert (label, sensitive) == ("Installed", False)

    best = types.SimpleNamespace(filename="shiny-new-mod-2.0.jar")
    label, sensitive = decide(
        host,
        _hit(),
        best,
        {"old-mod-1.0.jar"},
        is_modpack=False,
        is_datapack=False,
        is_plugin=False,
        btn_label="Install",
        installed_packs=set(),
        installed_dps=set(),
        installed_plugins=set(),
    )
    assert (label, sensitive) == ("Install", True)
