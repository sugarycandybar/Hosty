"""Tests for the flattened Modrinth navigation (no nested NavigationView).

A nested nav swallowed the swipe-back gesture on its root page, so touch
swipe could never leave the Modrinth search page. Search + detail pages
now live directly on the host nav like every other subpage.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from hosty.gtk_ui.views.files.main_view import FilesView  # noqa: E402


def _walk(widget):
    yield widget
    try:
        child = widget.get_first_child()
    except Exception:
        return
    while child is not None:
        yield from _walk(child)
        try:
            child = child.get_next_sibling()
        except Exception:
            return


def test_modrinth_has_no_nested_nav():
    view = FilesView()
    view._push_fullscreen_page_cb = None
    view._push_modrinth_page()
    inner_navs = [w for w in _walk(view._nav) if w is not view._nav and isinstance(w, Adw.NavigationView)]
    assert inner_navs == []
    tags = [w.get_tag() for w in _walk(view._nav) if isinstance(w, Adw.NavigationPage)]
    assert "modrinth-search" in tags


def test_popped_refresh_only_for_detail_pages():
    view = FilesView()
    calls = []
    view._refresh_modrinth_rows_install_state = lambda: calls.append(1)

    class _Page:
        def __init__(self, tag):
            self._tag = tag

        def get_tag(self):
            return self._tag

    view._on_modrinth_page_popped(None, _Page("modrinth-detail"))
    assert calls == [1]
    view._on_modrinth_page_popped(None, _Page("modrinth-search"))
    view._on_modrinth_page_popped(None, _Page(None))
    view._on_modrinth_page_popped(None, object())
    assert calls == [1]
