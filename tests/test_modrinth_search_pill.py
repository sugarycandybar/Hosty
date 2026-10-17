"""Tests for the Modrinth search pill layout.

Regression guard: the search spinner used to live inside the pill between the
entry and the filter button, which pushed the entry's clear button away from
the filter button while a search was loading. Loading now shows as a row in
the results list instead, so the pill only ever holds the entry + filter.
"""

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from hosty.gtk_ui.views.files.main_view import FilesView  # noqa: E402
from hosty.shared.backend import modrinth_client  # noqa: E402


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _find(widget, cls):
    for w in _walk(widget):
        if isinstance(w, cls):
            return w
    return None


def _find_by_class(widget, css_class):
    for w in _walk(widget):
        if w.has_css_class(css_class):
            return w
    return None


def _count(widget, cls):
    return sum(1 for w in _walk(widget) if isinstance(w, cls))


def _children(widget):
    out = []
    child = widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _pump_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _build_search_view():
    view = FilesView()
    view._push_fullscreen_page_cb = None
    view._push_modrinth_page()
    return view


def test_search_pill_has_no_spinner():
    """The pill must only contain the entry and the filter button."""
    view = _build_search_view()
    pill = _find_by_class(view, "modrinth-search-box")
    assert pill is not None
    kids = _children(pill)
    assert [type(k).__name__ for k in kids] == ["SearchEntry", "MenuButton"]
    assert _count(pill, Gtk.Spinner) == 0


def test_reset_search_shows_loading_row_in_list(monkeypatch):
    """While a search is in flight the spinner lives in the results list."""
    started = threading.Event()
    release = threading.Event()

    def fake_search(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return [], 0

    monkeypatch.setattr(modrinth_client, "search_mods", fake_search)

    view = _build_search_view()
    entry = _find(view, Gtk.SearchEntry)
    results = _find_by_class(view, "mod-results-list")

    entry.set_text("sodium")
    assert _pump_until(lambda: _count(results, Gtk.Spinner) >= 1)
    # The pill stays intact while the request is running.
    pill = _find_by_class(view, "modrinth-search-box")
    assert _count(pill, Gtk.Spinner) == 0
    # ...and the filter controls stay usable (not greyed out) while loading.
    filter_btn = _find_by_class(view, "modrinth-filter-btn")
    assert filter_btn is not None and filter_btn.get_sensitive()
    chips = [w for w in _walk(view) if w.has_css_class("modrinth-filter-option")]
    assert chips and all(c.get_sensitive() for c in chips)

    release.set()
    assert _pump_until(lambda: _count(results, Gtk.Spinner) == 0)
    assert results.get_row_at_index(0) is not None
