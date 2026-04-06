"""
ConnectView - Server connection tools (playit.gg tunnel).
"""
from __future__ import annotations

import json
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from hosty.backend.server_manager import ServerInfo, ServerManager


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


class ConnectView(Gtk.Box):
    """Connection tools for selected server."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._server_info: Optional[ServerInfo] = None
        self._server_manager: Optional[ServerManager] = None
        self._status_handler_id: Optional[int] = None

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title="Playit.gg",
            description="Expose your local server through a tunnel",
        )

        self._status_row = Adw.ActionRow(title="Tunnel status", subtitle="Stopped")
        self._status_row.set_activatable(False)
        group.add(self._status_row)

        self._binary_row = Adw.ActionRow(title="Binary", subtitle="Checking playit installation")
        self._binary_row.set_activatable(False)
        group.add(self._binary_row)

        self._secret_row = Adw.EntryRow(title="Playit Secret (optional)")
        self._secret_row.set_show_apply_button(True)
        self._secret_row.connect("apply", self._on_secret_apply)
        group.add(self._secret_row)

        start_row = Adw.ActionRow(title="Start tunnel", subtitle="Run playit for this server")
        start_row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        start_row.set_activatable(True)
        start_row.connect("activated", self._on_start)
        group.add(start_row)

        stop_row = Adw.ActionRow(title="Stop tunnel", subtitle="Stop active playit process")
        stop_row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-stop-symbolic"))
        stop_row.set_activatable(True)
        stop_row.connect("activated", self._on_stop)
        group.add(stop_row)

        download_row = Adw.ActionRow(title="Install playit binary", subtitle="Open playit.gg download page")
        download_row.add_prefix(Gtk.Image.new_from_icon_name("folder-download-symbolic"))
        download_row.set_activatable(True)
        download_row.connect("activated", lambda *_: _open_uri("https://playit.gg/download"))
        group.add(download_row)

        dashboard_row = Adw.ActionRow(title="Open playit dashboard", subtitle="Manage account and tunnels")
        dashboard_row.add_prefix(Gtk.Image.new_from_icon_name("applications-internet-symbolic"))
        dashboard_row.set_activatable(True)
        dashboard_row.connect("activated", lambda *_: _open_uri("https://playit.gg/account"))
        group.add(dashboard_row)

        page.add(group)

        hint_group = Adw.PreferencesGroup(title="Tip")
        hint = Adw.ActionRow(
            title="Start your Minecraft server first",
            subtitle="Then start playit to tunnel it to a public address.",
        )
        hint.set_activatable(False)
        hint_group.add(hint)
        page.add(hint_group)

        scrolled.set_child(page)
        self.append(scrolled)

    def set_server(self, server_info: ServerInfo, server_manager: ServerManager):
        self._server_info = server_info
        self._server_manager = server_manager

        playit = self._server_manager.playit_manager
        if self._status_handler_id is not None:
            try:
                playit.disconnect(self._status_handler_id)
            except Exception:
                pass
            self._status_handler_id = None

        self._status_handler_id = playit.connect("status-changed", self._on_playit_status_changed)
        self._refresh_binary_row()
        self._refresh_status_row()
        self._load_secret()

    def _server_dir(self) -> Optional[Path]:
        if not self._server_info:
            return None
        return Path(self._server_info.server_dir)

    def _secret_path(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-playit.json"

    def _load_secret(self):
        path = self._secret_path()
        if not path or not path.exists():
            self._secret_row.set_text("")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._secret_row.set_text(str(data.get("secret", "")))
        except Exception:
            self._secret_row.set_text("")

    def _save_secret(self):
        path = self._secret_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"secret": self._secret_row.get_text().strip()}, f, indent=2)
        except Exception:
            pass

    def _on_secret_apply(self, *_args):
        self._save_secret()
        self._toast("Playit secret saved")

    def _refresh_binary_row(self):
        if not self._server_manager:
            self._binary_row.set_subtitle("No server selected")
            return

        playit = self._server_manager.playit_manager
        bin_path = playit.resolve_binary()
        if bin_path:
            self._binary_row.set_subtitle(f"Installed at {bin_path}")
        else:
            self._binary_row.set_subtitle("Not installed. Use 'Install playit binary'.")

    def _refresh_status_row(self):
        if not self._server_manager:
            self._status_row.set_subtitle("Stopped")
            return

        playit = self._server_manager.playit_manager
        if playit.is_running:
            if self._server_info and playit.server_id == self._server_info.id:
                self._status_row.set_subtitle("Running for this server")
            else:
                self._status_row.set_subtitle("Running for another server")
        else:
            self._status_row.set_subtitle("Stopped")

    def _on_playit_status_changed(self, *_args):
        self._refresh_status_row()

    def _on_start(self, *_args):
        if not self._server_manager or not self._server_info:
            return

        self._save_secret()
        playit = self._server_manager.playit_manager
        ok, msg = playit.start(
            self._server_info.id,
            str(self._server_info.server_dir),
            secret=self._secret_row.get_text().strip(),
        )
        self._refresh_status_row()
        if ok:
            self._toast("Playit tunnel started")
        else:
            self._alert("Could not start playit", msg)

    def _on_stop(self, *_args):
        if not self._server_manager:
            return

        ok, msg = self._server_manager.playit_manager.stop()
        self._refresh_status_row()
        if ok:
            self._toast("Playit tunnel stopped")
        else:
            self._alert("Could not stop playit", msg)

    def _alert(self, title: str, body: str):
        d = Adw.AlertDialog()
        d.set_heading(title)
        d.set_body(body)
        d.add_response("ok", "OK")
        d.present(self.get_root())

    def _toast(self, message: str):
        root = self.get_root()
        if root and hasattr(root, "show_toast"):
            root.show_toast(message)
