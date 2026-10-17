"""Tests for the OSD progress bar and the mod-install progress reporter.

The bar is a ``Gtk.ProgressBar`` with the libadwaita ``osd`` class, added as
a ``GtkOverlay`` child at the top of the window. These tests run without a
display (widgets are constructed but never presented).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from hosty.gtk_ui.views.files.mixins import mods_mixin as mods_mixin_mod  # noqa: E402
from hosty.gtk_ui.views.files.mixins.mods_mixin import ModsMixin  # noqa: E402
from hosty.gtk_ui.widgets.osd_progress import OsdProgressBar  # noqa: E402


def test_bar_starts_hidden_with_osd_class():
    bar = OsdProgressBar()
    assert bar.has_css_class("osd")
    assert bar.get_visible() is False
    assert bar.get_valign() == Gtk.Align.START


def test_begin_and_end_toggle_visibility():
    bar = OsdProgressBar()
    bar.begin()
    assert bar.get_visible() is True
    bar.end()
    assert bar.get_visible() is False


def test_overlapping_operations_keep_bar_visible():
    bar = OsdProgressBar()
    bar.begin()
    bar.begin()
    bar.end()
    assert bar.get_visible() is True
    bar.end()
    assert bar.get_visible() is False


def test_update_clamps_fraction_and_stops_pulse():
    bar = OsdProgressBar()
    bar.begin()  # starts pulsing
    bar.update(2.5)
    assert bar.get_fraction() == 1.0
    assert bar._pulse_id == 0
    bar.update(-3)
    assert bar.get_fraction() == 0.0
    bar.update("bad")
    assert bar.get_fraction() == 0.0
    bar.end()


def test_update_none_restarts_pulse():
    bar = OsdProgressBar()
    bar.begin()
    bar.update(0.5)
    assert bar._pulse_id == 0
    bar.update(None)
    assert bar._pulse_id != 0
    bar.end()
    assert bar._pulse_id == 0


class _Reporter(ModsMixin):
    """Bare mixin instance capturing reported fractions."""

    def __init__(self):
        self.values = []

    def _progress_update(self, fraction):
        self.values.append(fraction)


class _FakeRoot:
    def __init__(self):
        self.calls = []

    def begin_progress(self):
        self.calls.append("begin")

    def update_progress(self, fraction):
        self.calls.append(("update", fraction))

    def end_progress(self):
        self.calls.append("end")


class _PlainView(ModsMixin):
    """Mixin instance with no overrides, for forwarding tests."""


def test_mixin_helpers_forward_to_window():
    view = _PlainView()
    root = _FakeRoot()
    view.get_root = lambda: root

    view._progress_begin()
    view._progress_update(0.25)
    view._progress_end()

    assert root.calls == ["begin", ("update", 0.25), "end"]


def test_mixin_helpers_tolerate_plain_window():
    """A root without the progress API (e.g. plain test window) is a no-op."""
    view = _PlainView()
    view.get_root = lambda: object()
    view._progress_begin()
    view._progress_update(0.5)
    view._progress_end()


def test_reporter_reports_overall_fraction(monkeypatch):
    monkeypatch.setattr(mods_mixin_mod.GLib, "idle_add", lambda fn, *args: fn(*args))
    reporter = _Reporter()

    # File 2 of 3, half downloaded -> (1 + 0.5) / 3.
    reporter._install_progress_reporter(index=1, count=3)(50, 100)
    assert reporter.values == [0.5]

    # Unknown total falls back to the file boundary itself.
    reporter.values.clear()
    reporter._install_progress_reporter(index=2, count=3)(0, 0)
    assert reporter.values == [2 / 3]

    # Never exceeds 1.0.
    reporter.values.clear()
    reporter._install_progress_reporter(index=0, count=1)(999, 100)
    assert reporter.values == [1.0]
