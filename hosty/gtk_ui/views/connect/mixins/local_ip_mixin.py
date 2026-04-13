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

from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.gtk_ui.dialogs.playit_setup import PlayitSetupDialog


PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"



from ..utils import *

class LocalIpMixin:
    def _make_local_network_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Local Network",
            description="Share your LAN address for local multiplayer",
        )
        row = Adw.ActionRow(title="Local device IP", subtitle="Detecting...")
        row.set_activatable(False)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy local IP")
        copy_btn.connect("clicked", self._on_copy_local_ip)
        row.add_suffix(copy_btn)
        group.add(row)
        self._local_ip_rows.append(row)
        return group

    def _get_local_ip(self) -> str:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        try:
            ip = socket.gethostbyname(socket.gethostname())
            if ip:
                return ip
        except Exception:
            pass

        return "Not available"

    def _refresh_local_ip_row(self):
        ip = self._get_local_ip()
        self._local_ip_value = ip
        for row in self._local_ip_rows:
            row.set_subtitle(ip)

    def _on_copy_local_ip(self, *_args):
        ip = self._local_ip_value.strip()
        if not ip or ip == "Not available":
            self._toast("Local IP not available")
            return
        try:
            display = Gdk.Display.get_default()
            if display:
                clipboard = display.get_clipboard()
                clipboard.set(ip)
                self._toast("Local IP copied")
                return
        except Exception:
            pass
        self._toast("Could not access clipboard")

