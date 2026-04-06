"""
TrayManager - Optional system tray integration for background mode.
"""
from __future__ import annotations

from typing import Callable, Optional

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except Exception:
    pystray = None
    Image = None
    ImageDraw = None
    HAS_TRAY = False


def _draw_default_icon() -> "Image.Image":
    """Create a compact Hosty tray icon when no file asset is available."""
    image = Image.new("RGBA", (64, 64), (32, 35, 48, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill=(57, 102, 198, 255))
    draw.rectangle((28, 18, 36, 46), fill=(255, 255, 255, 255))
    draw.rectangle((18, 28, 46, 36), fill=(255, 255, 255, 255))
    return image


class TrayManager:
    """Manage a tray icon lifecycle using pystray when available."""

    def __init__(self, on_restore: Callable[[], None], on_quit: Callable[[], None]):
        self._on_restore = on_restore
        self._on_quit = on_quit
        self._icon: Optional["pystray.Icon"] = None

    @property
    def available(self) -> bool:
        return HAS_TRAY

    @property
    def active(self) -> bool:
        return self._icon is not None

    def show(self) -> bool:
        """Show tray icon. Returns False if tray backend is unavailable."""
        if not HAS_TRAY:
            return False
        if self._icon is not None:
            return True

        def on_restore(_icon, _item):
            GLib.idle_add(self._on_restore)

        def on_quit(_icon, _item):
            GLib.idle_add(self._on_quit)

        self._icon = pystray.Icon(
            "hosty",
            _draw_default_icon(),
            "Hosty",
            menu=pystray.Menu(
                pystray.MenuItem("Open Hosty", on_restore, default=True),
                pystray.MenuItem("Quit Hosty", on_quit),
            ),
        )

        self._icon.run_detached()
        return True

    def hide(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            pass
        self._icon = None
