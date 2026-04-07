"""
ConnectView - Server connection tools (playit.gg tunnel).
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import webbrowser
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
        self._cfg = {}
        self._suppress_config_updates = False
        self._start_in_progress = False
        self._local_ip_rows: list[Adw.ActionRow] = []
        self._local_ip_value = "Not available"

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._mode_stack = Gtk.Stack()
        self._mode_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._mode_stack.set_transition_duration(180)

        self._mode_stack.add_named(self._build_setup_required_page(), "setup")
        self._mode_stack.add_named(self._build_ready_page(), "ready")

        scrolled.set_child(self._mode_stack)
        self.append(scrolled)

        self._refresh_local_ip_row()

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

    def _build_setup_required_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        page.add(self._make_local_network_group())

        setup_group = Adw.PreferencesGroup(
            title="Playit.gg",
            description="Set up Playit to expose your server publicly",
        )
        setup_row = Adw.ActionRow(
            title="Playit setup required",
            subtitle="Install and claim Playit for this server",
        )

        self._setup_btn = Gtk.Button(label="Set Up Playit")
        self._setup_btn.add_css_class("suggested-action")
        self._setup_btn.set_valign(Gtk.Align.CENTER)
        self._setup_btn.connect("clicked", self._on_open_setup_dialog)
        setup_row.add_suffix(self._setup_btn)
        setup_group.add(setup_row)

        page.add(setup_group)

        return page

    def _build_ready_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        page.add(self._make_local_network_group())

        group = Adw.PreferencesGroup(
            title="Playit.gg",
            description="Simple tunnel controls",
        )

        self._tunnel_row = Adw.ActionRow(title="Tunnel", subtitle="Stopped")
        self._tunnel_row.set_activatable(False)
        self._tunnel_btn = Gtk.Button(label="Start")
        self._tunnel_btn.add_css_class("suggested-action")
        self._tunnel_btn.connect("clicked", self._on_tunnel_toggle)
        self._tunnel_row.add_suffix(self._tunnel_btn)
        group.add(self._tunnel_row)

        dashboard_row = Adw.ActionRow(title="Open playit dashboard", subtitle="View tunnel address and details")
        dashboard_row.add_prefix(Gtk.Image.new_from_icon_name("applications-internet-symbolic"))
        dashboard_row.set_activatable(True)
        dashboard_row.connect("activated", self._on_open_dashboard)
        group.add(dashboard_row)

        self._auto_start_row = Adw.SwitchRow(
            title="Start with server",
            subtitle="Automatically start and stop with this server",
        )
        self._auto_start_row.connect("notify::active", self._on_auto_start_toggled)
        group.add(self._auto_start_row)

        reset_row = Adw.ActionRow(title="Set Up Playit Again", subtitle="Re-run guided setup")
        reset_row.add_prefix(Gtk.Image.new_from_icon_name("emblem-synchronizing-symbolic"))
        reset_row.set_activatable(True)
        reset_row.connect("activated", self._on_open_setup_dialog)
        group.add(reset_row)

        page.add(group)
        return page

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
        self._refresh_local_ip_row()
        self._load_server_config()
        self._refresh_mode()
        self._refresh_status_row()

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

    def _server_dir(self) -> Optional[Path]:
        if not self._server_info:
            return None
        return Path(self._server_info.server_dir)

    def _load_server_config(self):
        root = self._server_dir()
        if not root:
            self._cfg = {}
            return
        self._cfg = load_playit_config(root)

        if self._server_manager:
            claimed_secret = self._server_manager.playit_manager.read_claimed_secret()
            cfg_changed = False
            if claimed_secret and claimed_secret != str(self._cfg.get("secret", "")).strip():
                self._cfg["secret"] = claimed_secret
                cfg_changed = True

            # If playit is already claimed globally, auto-heal per-server flags.
            if claimed_secret and not bool(self._cfg.get("enabled", False)):
                self._cfg["enabled"] = True
                cfg_changed = True
            if claimed_secret and not bool(self._cfg.get("setup_complete", False)):
                self._cfg["setup_complete"] = True
                cfg_changed = True

            if cfg_changed:
                save_playit_config(root, self._cfg)

        self._suppress_config_updates = True
        self._auto_start_row.set_active(bool(self._cfg.get("auto_start", True)))
        self._suppress_config_updates = False

    def _save_server_config(self, updates: Optional[dict] = None) -> bool:
        root = self._server_dir()
        if not root:
            return False

        if updates:
            self._cfg.update(updates)

        return save_playit_config(
            root,
            {
                "secret": str(self._cfg.get("secret", "")).strip(),
                "enabled": bool(self._cfg.get("enabled", False)),
                "setup_complete": bool(self._cfg.get("setup_complete", False)),
                "auto_start": self._auto_start_row.get_active(),
                "auto_install": bool(self._cfg.get("auto_install", True)),
            },
        )

    def _on_auto_start_toggled(self, *_args):
        if self._suppress_config_updates:
            return
        self._save_server_config()

    def _is_setup_complete(self) -> bool:
        if not self._server_manager:
            return False
        return bool(
            self._cfg.get("enabled", False)
            and self._cfg.get("setup_complete", False)
            and (
                self._server_manager.playit_manager.has_claimed_secret()
                or bool(str(self._cfg.get("secret", "")).strip())
            )
        )

    def _refresh_mode(self):
        mode = "ready" if self._is_setup_complete() else "setup"
        self._mode_stack.set_visible_child_name(mode)

    def _refresh_status_row(self):
        if not self._server_manager:
            self._tunnel_row.set_subtitle("Stopped")
            self._tunnel_btn.set_label("Start")
            self._tunnel_btn.remove_css_class("destructive-action")
            self._tunnel_btn.add_css_class("suggested-action")
            return

        playit = self._server_manager.playit_manager
        if playit.is_running:
            if self._server_info and playit.server_id == self._server_info.id:
                self._tunnel_row.set_subtitle("Running for this server")
                self._tunnel_btn.set_label("Stop")
                self._tunnel_btn.remove_css_class("suggested-action")
                self._tunnel_btn.add_css_class("destructive-action")
                self._tunnel_btn.set_sensitive(True)
            else:
                self._tunnel_row.set_subtitle("Running for another server")
                self._tunnel_btn.set_label("Start")
                self._tunnel_btn.remove_css_class("destructive-action")
                self._tunnel_btn.add_css_class("suggested-action")
                self._tunnel_btn.set_sensitive(False)
        else:
            self._tunnel_row.set_subtitle("Stopped")
            self._tunnel_btn.set_label("Start")
            self._tunnel_btn.remove_css_class("destructive-action")
            self._tunnel_btn.add_css_class("suggested-action")
            self._tunnel_btn.set_sensitive(True)

        if self._start_in_progress:
            self._tunnel_btn.set_label("Starting...")
            self._tunnel_btn.set_sensitive(False)

    def _on_playit_status_changed(self, *_args):
        self._refresh_status_row()

    def _on_tunnel_toggle(self, *_args):
        if not self._server_manager:
            return
        playit = self._server_manager.playit_manager
        if playit.is_running and self._server_info and playit.server_id == self._server_info.id:
            self._on_stop()
        else:
            self._on_start()

    def _server_running(self) -> bool:
        if not self._server_manager or not self._server_info:
            return False
        process = self._server_manager.get_process(self._server_info.id)
        return bool(process and process.is_running)

    def _on_open_setup_dialog(self, *_args):
        if not self._server_manager or not self._server_info:
            return

        dialog = PlayitSetupDialog(
            self._server_manager,
            self._server_info,
            self._server_running(),
        )
        dialog.connect("setup-complete", self._on_setup_complete)
        dialog.present(self.get_root())
        dialog.start_setup()

    def _on_setup_complete(self, *_args):
        self._load_server_config()
        self._refresh_mode()
        self._refresh_status_row()
        self._toast("Playit setup completed")

    def _on_open_dashboard(self, *_args):
        if not _open_uri(PLAYIT_DASHBOARD_URL):
            self._alert("Could not open browser", "Unable to open playit dashboard.")

    def _on_start(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if self._start_in_progress:
            self._toast("Playit startup is already in progress")
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        self._start_in_progress = True

        def worker():
            playit = self._server_manager.playit_manager
            return playit.start(
                server_id,
                server_dir,
                secret=secret,
                auto_install=True,
            )

        def run():
            ok, msg = worker()

            def ui_done():
                self._start_in_progress = False
                self._refresh_status_row()
                if ok:
                    self._toast("Playit tunnel started")
                else:
                    self._alert("Could not start playit", msg)

            GLib.idle_add(ui_done)

        threading.Thread(target=run, daemon=True).start()

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
