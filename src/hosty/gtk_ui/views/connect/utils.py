"""
ConnectView - Server connection tools (playit.gg tunnel).
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk

from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs

PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"


__all__ = [
    "_is_descendant_of",
    "_open_uri",
]


def _is_descendant_of(widget: Gtk.Widget, ancestor: Gtk.Widget) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = current.get_parent()
    return False


def _open_uri(uri: str) -> bool:
    try:
        if webbrowser.open(uri):
            return True
    except Exception:
        pass

    try:
        cmd = ["open", uri] if sys.platform == "darwin" else ["xdg-open", uri]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            **hidden_subprocess_kwargs(),
        )
        return True
    except Exception:
        return False
