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


class ConnectView(Gtk.Box):
    """Connection tools for selected server."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._server_info: Optional[ServerInfo] = None
        self._server_manager: Optional[ServerManager] = None
        self._status_handler_id: Optional[int] = None
        self._manager_changed_id: Optional[int] = None
        self._cfg = {}
        self._suppress_config_updates = False
        self._start_in_progress = False
        self._local_ip_rows: list[Adw.ActionRow] = []
        self._local_ip_value = "Not available"
        self._players_name_rows: list[Adw.EntryRow] = []
        self._whitelist_status_rows: list[Adw.ActionRow] = []
        self._whitelist_groups: list[Adw.PreferencesGroup] = []
        self._banned_groups: list[Adw.PreferencesGroup] = []
        self._player_rows_by_group: dict[Adw.PreferencesGroup, list[Gtk.Widget]] = {}

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
        self._append_players_groups(page)

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
        reset_row.add_prefix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        reset_row.set_activatable(True)
        reset_row.connect("activated", self._on_open_setup_dialog)
        group.add(reset_row)

        page.add(group)
        self._append_players_groups(page)
        return page

    def _append_players_groups(self, page: Adw.PreferencesPage):
        actions = Adw.PreferencesGroup(
            title="Players",
            description="Manage whitelist and banned players",
        )

        name_row = Adw.EntryRow(title="Player name")
        name_row.set_show_apply_button(False)
        actions.add(name_row)
        self._players_name_rows.append(name_row)

        add_row = Adw.ActionRow(title="Add to whitelist", subtitle="Allow this player to join")
        add_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_row.set_activatable(True)
        add_row.connect("activated", lambda *_args, r=name_row: self._on_add_whitelist(r))
        actions.add(add_row)

        ban_row = Adw.ActionRow(title="Ban player", subtitle="Block this player from joining")
        ban_row.add_prefix(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        ban_row.set_activatable(True)
        ban_row.connect("activated", lambda *_args, r=name_row: self._on_add_banned(r))
        actions.add(ban_row)
        page.add(actions)

        whitelist_group = Adw.PreferencesGroup(title="Whitelist")
        banned_group = Adw.PreferencesGroup(title="Banned Players")

        wl_status = Adw.ActionRow(title="Status", subtitle="Checking...")
        wl_status.set_activatable(False)
        whitelist_group.add(wl_status)
        self._whitelist_status_rows.append(wl_status)

        self._whitelist_groups.append(whitelist_group)
        self._banned_groups.append(banned_group)
        self._player_rows_by_group[whitelist_group] = []
        self._player_rows_by_group[banned_group] = []
        page.add(whitelist_group)
        page.add(banned_group)

    def set_server(self, server_info: ServerInfo, server_manager: ServerManager):
        if self._server_manager and self._manager_changed_id is not None:
            try:
                self._server_manager.disconnect(self._manager_changed_id)
            except Exception:
                pass
            self._manager_changed_id = None

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
        self._manager_changed_id = self._server_manager.connect("server-changed", self._on_server_changed)
        self._refresh_local_ip_row()
        self._load_server_config()
        self._refresh_whitelist_status()
        self._refresh_player_lists()
        self._refresh_mode()
        self._refresh_status_row()

    def _on_server_changed(self, _manager, server_id):
        if not self._server_info or server_id != self._server_info.id:
            return
        self._refresh_whitelist_status()
        self._refresh_player_lists()

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

    def _player_list_paths(self) -> tuple[Optional[Path], Optional[Path]]:
        root = self._server_dir()
        if not root:
            return None, None
        return root / "whitelist.json", root / "banned-players.json"

    def _read_player_list(self, path: Optional[Path]) -> list[dict]:
        if not path or not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return []
        if not isinstance(raw, list):
            return []

        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            out.append(item)
        return sorted(out, key=lambda e: str(e.get("name", "")).lower())

    def _write_player_list(self, path: Optional[Path], entries: list[dict]) -> bool:
        if not path:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
            return True
        except Exception:
            return False

    def _clear_player_group_rows(self, group: Adw.PreferencesGroup):
        rows = self._player_rows_by_group.get(group, [])
        for row in list(rows):
            try:
                if _is_descendant_of(row, group):
                    group.remove(row)
            except Exception:
                pass
        rows.clear()

    def _add_info_row(self, group: Adw.PreferencesGroup, title: str):
        row = Adw.ActionRow(title=title)
        row.set_activatable(False)
        group.add(row)
        self._player_rows_by_group[group].append(row)

    def _refresh_whitelist_status(self):
        enabled = False
        if self._server_manager and self._server_info:
            cfg = self._server_manager.get_config(self._server_info.id)
            if cfg:
                cfg.load()
                enabled = cfg.get_bool("white-list", False)

        subtitle = "Enabled" if enabled else "Disabled"
        for row in self._whitelist_status_rows:
            row.set_subtitle(subtitle)

    def _refresh_player_lists(self):
        for group in self._whitelist_groups:
            self._clear_player_group_rows(group)
        for group in self._banned_groups:
            self._clear_player_group_rows(group)

        whitelist_path, banned_path = self._player_list_paths()
        if not whitelist_path or not banned_path:
            for group in self._whitelist_groups:
                self._add_info_row(group, "No server selected")
            for group in self._banned_groups:
                self._add_info_row(group, "No server selected")
            return

        whitelist = self._read_player_list(whitelist_path)
        banned = self._read_player_list(banned_path)

        if not whitelist:
            for group in self._whitelist_groups:
                self._add_info_row(group, "No whitelisted players")
        else:
            for entry in whitelist:
                name = str(entry.get("name", "")).strip()
                subtitle = str(entry.get("uuid", "Unknown UUID"))
                for group in self._whitelist_groups:
                    row = Adw.ActionRow(title=name)
                    row.set_subtitle(subtitle)
                    row.set_activatable(False)
                    remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
                    remove_btn.add_css_class("flat")
                    remove_btn.add_css_class("destructive-action")
                    remove_btn.set_tooltip_text("Remove from whitelist")
                    remove_btn.connect("clicked", lambda *_a, n=name: self._remove_whitelist_player(n))
                    row.add_suffix(remove_btn)
                    group.add(row)
                    self._player_rows_by_group[group].append(row)

        if not banned:
            for group in self._banned_groups:
                self._add_info_row(group, "No banned players")
        else:
            for entry in banned:
                name = str(entry.get("name", "")).strip()
                reason = str(entry.get("reason", "Banned")).strip()
                for group in self._banned_groups:
                    row = Adw.ActionRow(title=name)
                    row.set_subtitle(reason)
                    row.set_activatable(False)
                    remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
                    remove_btn.add_css_class("flat")
                    remove_btn.add_css_class("destructive-action")
                    remove_btn.set_tooltip_text("Pardon player")
                    remove_btn.connect("clicked", lambda *_a, n=name: self._remove_banned_player(n))
                    row.add_suffix(remove_btn)
                    group.add(row)
                    self._player_rows_by_group[group].append(row)

    def _entered_player_name(self, preferred_row: Optional[Adw.EntryRow] = None) -> str:
        if preferred_row:
            txt = preferred_row.get_text().strip()
            if txt:
                return txt
        for row in self._players_name_rows:
            txt = row.get_text().strip()
            if txt:
                return txt
        return ""

    def _dash_uuid(self, value: str) -> str:
        raw = value.strip()
        if len(raw) != 32:
            return raw
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"

    def _resolve_profile(self, name: str) -> tuple[str, str]:
        try:
            quoted = urllib.parse.quote(name)
            req = urllib.request.Request(
                f"https://api.mojang.com/users/profiles/minecraft/{quoted}",
                headers={"User-Agent": "Hosty/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if resp.status == 204:
                    return name, ""
                data = json.loads(resp.read().decode("utf-8"))
            resolved_name = str(data.get("name", name)).strip() or name
            resolved_uuid = self._dash_uuid(str(data.get("id", "")).strip())
            return resolved_name, resolved_uuid
        except Exception:
            return name, ""

    def _on_add_whitelist(self, preferred_row: Optional[Adw.EntryRow] = None):
        self._add_player("whitelist", preferred_row)

    def _on_add_banned(self, preferred_row: Optional[Adw.EntryRow] = None):
        self._add_player("banned", preferred_row)

    def _add_player(self, list_type: str, preferred_row: Optional[Adw.EntryRow] = None):
        name = self._entered_player_name(preferred_row)
        if not name:
            self._alert("Missing player name", "Enter a player name first.")
            return

        whitelist_path, banned_path = self._player_list_paths()
        path = whitelist_path if list_type == "whitelist" else banned_path
        if not path:
            self._alert("No server selected", "Select a server first.")
            return

        process = None
        if self._server_manager and self._server_info:
            process = self._server_manager.get_process(self._server_info.id)
        if process and process.is_running:
            if list_type == "whitelist":
                process.send_command(f"whitelist add {name}")
            else:
                process.send_command(f"ban {name}")

        def worker():
            resolved_name, resolved_uuid = self._resolve_profile(name)

            def ui_apply():
                entries = self._read_player_list(path)
                if any(str(e.get("name", "")).lower() == resolved_name.lower() for e in entries):
                    self._toast(f"{resolved_name} is already listed")
                    return

                if list_type == "whitelist":
                    entries.append({"uuid": resolved_uuid, "name": resolved_name})
                    saved = self._write_player_list(path, entries)
                    if saved:
                        self._toast(f"Added {resolved_name} to whitelist")
                else:
                    entries.append({
                        "uuid": resolved_uuid,
                        "name": resolved_name,
                        "created": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S +0000"),
                        "source": "Hosty",
                        "expires": "forever",
                        "reason": "Banned by Hosty",
                    })
                    saved = self._write_player_list(path, entries)
                    if saved:
                        self._toast(f"Banned {resolved_name}")

                if saved:
                    self._refresh_player_lists()
                    for row in self._players_name_rows:
                        row.set_text("")
                else:
                    self._alert("Could not save", "Failed to write player list file.")

            GLib.idle_add(ui_apply)

        threading.Thread(target=worker, daemon=True).start()

    def _remove_whitelist_player(self, name: str):
        whitelist_path, _ = self._player_list_paths()
        if not whitelist_path:
            return

        entries = self._read_player_list(whitelist_path)
        new_entries = [e for e in entries if str(e.get("name", "")).lower() != name.lower()]
        if len(new_entries) == len(entries):
            return

        if self._write_player_list(whitelist_path, new_entries):
            process = None
            if self._server_manager and self._server_info:
                process = self._server_manager.get_process(self._server_info.id)
            if process and process.is_running:
                process.send_command(f"whitelist remove {name}")
            self._refresh_player_lists()
            self._toast(f"Removed {name} from whitelist")

    def _remove_banned_player(self, name: str):
        _, banned_path = self._player_list_paths()
        if not banned_path:
            return

        entries = self._read_player_list(banned_path)
        new_entries = [e for e in entries if str(e.get("name", "")).lower() != name.lower()]
        if len(new_entries) == len(entries):
            return

        if self._write_player_list(banned_path, new_entries):
            process = None
            if self._server_manager and self._server_info:
                process = self._server_manager.get_process(self._server_info.id)
            if process and process.is_running:
                process.send_command(f"pardon {name}")
            self._refresh_player_lists()
            self._toast(f"Unbanned {name}")

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

        if self._server_info and self._auto_start_row.get_active():
            root = self.get_root()
            if root and hasattr(root, "clear_playit_auto_start_pause"):
                root.clear_playit_auto_start_pause(self._server_info.id)

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

        if self._server_info and self._auto_start_row.get_active() and self._server_running():
            root = self.get_root()
            if root and hasattr(root, "pause_playit_auto_start_for_running_server"):
                root.pause_playit_auto_start_for_running_server(self._server_info.id)

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
