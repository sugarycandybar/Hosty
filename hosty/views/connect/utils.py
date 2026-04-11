"""
ConnectView - Server connection tools (playit.gg tunnel).
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Adw, Gdk, GLib

from hosty.backend.playit_config import load_playit_config, save_playit_config
from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.dialogs.playit_setup import PlayitSetupDialog


PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"



__all__ = [
    '_is_descendant_of',
    '_open_uri',
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
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


