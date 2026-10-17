"""A thin, top-of-window OSD progress bar.

libadwaita styles a ``GtkProgressBar`` carrying the ``osd`` class as a
thin strip without a visible trough, meant to be added as a ``GtkOverlay``
child attached to the top of a window. Hosty uses it to show install
progress (mods, plugins, datapacks, modpacks) over the whole window.

Installs are reference-counted: ``begin``/``end`` bracket one operation, so
two overlapping installs don't hide the bar while the other is still
running. Before a percentage is known the bar pulses (indeterminate).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

# Update cadence for the indeterminate pulse (ms).
_PULSE_INTERVAL_MS = 120


class OsdProgressBar(Gtk.ProgressBar):
    """Top-of-window progress strip with reference-counted visibility."""

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("osd")
        self.set_valign(Gtk.Align.START)
        self.set_halign(Gtk.Align.FILL)
        self.set_can_focus(False)
        self.set_show_text(False)
        self.set_visible(False)
        self._active = 0
        self._pulse_id = 0

    # -- reference-counted lifecycle ------------------------------------
    def begin(self) -> None:
        """Mark one operation as running and show the bar."""
        self._active += 1
        if self._active == 1:
            self.set_visible(True)
            self._start_pulse()

    def update(self, fraction: float | None) -> None:
        """Set determinate progress (0.0-1.0), or ``None`` to pulse."""
        if fraction is None:
            self._start_pulse()
            return
        self._stop_pulse()
        try:
            value = float(fraction)
        except (TypeError, ValueError):
            value = 0.0
        self.set_fraction(min(1.0, max(0.0, value)))

    def end(self) -> None:
        """Mark one operation as finished; hide the bar once none remain."""
        self._active = max(0, self._active - 1)
        if self._active == 0:
            self._stop_pulse()
            self.set_visible(False)

    # -- indeterminate pulse --------------------------------------------
    def _start_pulse(self) -> None:
        if self._pulse_id:
            return
        self.pulse()
        self._pulse_id = GLib.timeout_add(_PULSE_INTERVAL_MS, self._on_pulse)

    def _on_pulse(self) -> bool:
        if self._active == 0:
            self._pulse_id = 0
            return False
        self.pulse()
        return True

    def _stop_pulse(self) -> None:
        if self._pulse_id:
            try:
                GLib.source_remove(self._pulse_id)
            except Exception:
                pass
            self._pulse_id = 0
