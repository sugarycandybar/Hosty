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

class PlayersMixin:
    def _append_players_groups(self, page: Adw.PreferencesPage):
        actions = Adw.PreferencesGroup(
            title="Players",
            description="Manage whitelist and banned players",
        )

        name_row = Adw.EntryRow(title="Player name")
        name_row.set_show_apply_button(False)
        actions.add(name_row)
        self._players_name_rows.append(name_row)

        add_row = Adw.ActionRow(title="Add to whitelist")
        add_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_row.set_activatable(True)
        add_row.connect("activated", lambda *_args, r=name_row: self._on_add_whitelist(r))
        actions.add(add_row)

        ban_row = Adw.ActionRow(title="Ban player")
        ban_row.add_prefix(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        ban_row.set_activatable(True)
        ban_row.connect("activated", lambda *_args, r=name_row: self._on_add_banned(r))
        actions.add(ban_row)
        page.add(actions)

        whitelist_group = Adw.PreferencesGroup(title="Whitelist")
        banned_group = Adw.PreferencesGroup(title="Banned Players")

        wl_toggle = Adw.SwitchRow(
            title="Whitelist enabled",
            subtitle="Only whitelisted players can join",
        )
        wl_toggle.connect("notify::active", self._on_whitelist_toggled)
        whitelist_group.add(wl_toggle)
        self._whitelist_toggle_rows.append(wl_toggle)

        wl_show = Adw.ExpanderRow(
            title="Show all whitelisted",
            subtitle="No players",
        )
        wl_show.set_expanded(False)
        whitelist_group.add(wl_show)
        self._whitelist_list_rows.append(wl_show)

        banned_show = Adw.ExpanderRow(
            title="Show all banned",
            subtitle="No players",
        )
        banned_show.set_expanded(False)
        banned_group.add(banned_show)
        self._banned_list_rows.append(banned_show)

        self._whitelist_groups.append(whitelist_group)
        self._banned_groups.append(banned_group)
        self._player_rows_by_group[wl_show] = []
        self._player_rows_by_group[banned_show] = []
        page.add(whitelist_group)
        page.add(banned_group)

    def _update_player_section_summaries(self, whitelist_count: int, banned_count: int):
        wl_summary = f"{whitelist_count} player{'s' if whitelist_count != 1 else ''}"
        for row in self._whitelist_list_rows:
            row.set_subtitle(wl_summary)

        ban_summary = f"{banned_count} player{'s' if banned_count != 1 else ''}"
        for row in self._banned_list_rows:
            row.set_subtitle(ban_summary)

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

    def _clear_player_group_rows(self, container: Gtk.Widget):
        rows = self._player_rows_by_group.get(container, [])
        for row in list(rows):
            try:
                container.remove(row)
            except Exception:
                pass
        rows.clear()

    def _add_info_row(self, container: Adw.ExpanderRow, title: str):
        row = Adw.ActionRow(title=title)
        row.set_activatable(False)
        container.add_row(row)
        self._player_rows_by_group[container].append(row)

    def _refresh_player_lists(self):
        for row in self._whitelist_list_rows:
            self._clear_player_group_rows(row)
        for row in self._banned_list_rows:
            self._clear_player_group_rows(row)

        whitelist_path, banned_path = self._player_list_paths()
        if not whitelist_path or not banned_path:
            self._update_player_section_summaries(0, 0)
            for row in self._whitelist_list_rows:
                self._add_info_row(row, "No server selected")
            for row in self._banned_list_rows:
                self._add_info_row(row, "No server selected")
            return

        whitelist = self._read_player_list(whitelist_path)
        banned = self._read_player_list(banned_path)

        if not whitelist:
            for container in self._whitelist_list_rows:
                self._add_info_row(container, "No whitelisted players")
        else:
            for entry in whitelist:
                name = str(entry.get("name", "")).strip()
                subtitle = str(entry.get("uuid", "Unknown UUID"))
                for container in self._whitelist_list_rows:
                    row = Adw.ActionRow(title=name)
                    row.set_subtitle(subtitle)
                    row.set_activatable(False)
                    remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
                    remove_btn.add_css_class("flat")
                    remove_btn.add_css_class("destructive-action")
                    remove_btn.set_tooltip_text("Remove from whitelist")
                    remove_btn.connect("clicked", lambda *_a, n=name: self._remove_whitelist_player(n))
                    row.add_suffix(remove_btn)

                    container.add_row(row)
                    self._player_rows_by_group[container].append(row)

        if not banned:
            for container in self._banned_list_rows:
                self._add_info_row(container, "No banned players")
        else:
            for entry in banned:
                name = str(entry.get("name", "")).strip()
                reason = str(entry.get("reason", "Banned")).strip()
                for container in self._banned_list_rows:
                    row = Adw.ActionRow(title=name)
                    row.set_subtitle(reason)
                    row.set_activatable(False)
                    remove_btn = Gtk.Button(label="Pardon")
                    remove_btn.add_css_class("flat")
                    remove_btn.set_tooltip_text("Pardon player")
                    remove_btn.connect("clicked", lambda *_a, n=name: self._remove_banned_player(n))
                    row.add_suffix(remove_btn)

                    container.add_row(row)
                    self._player_rows_by_group[container].append(row)

        self._update_player_section_summaries(len(whitelist), len(banned))

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
        name = self._entered_player_name(preferred_row)
        if not name:
            self._alert("Missing player name", "Enter a player name first.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Ban player")
        dialog.set_body(f"Ban “{name}”? Add a reason below.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ban", "Ban")
        dialog.set_response_appearance("ban", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("ban")
        dialog.set_close_response("cancel")

        reason_entry = Gtk.Entry()
        reason_entry.set_placeholder_text("Reason")
        reason_entry.set_text("Banned by Hosty")
        dialog.set_extra_child(reason_entry)

        def on_response(_d, response):
            if response == "ban":
                reason = self._normalize_ban_reason(reason_entry.get_text())
                self._add_player("banned", preferred_row, ban_reason=reason)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _normalize_ban_reason(self, reason: str) -> str:
        cleaned = " ".join(str(reason or "").split())
        return cleaned or "Banned by Hosty"

    def _add_player(self, list_type: str, preferred_row: Optional[Adw.EntryRow] = None, ban_reason: str = "Banned by Hosty"):
        name = self._entered_player_name(preferred_row)
        if not name:
            self._alert("Missing player name", "Enter a player name first.")
            return

        reason_text = self._normalize_ban_reason(ban_reason)

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
                process.send_command(f"ban {name} {reason_text}")

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
                        "reason": reason_text,
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
        removed = [e for e in entries if str(e.get("name", "")).lower() == name.lower()]
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

            def undo_remove():
                current = self._read_player_list(whitelist_path)
                existing_names = {str(e.get("name", "")).lower() for e in current}
                merged = list(current)
                for item in removed:
                    iname = str(item.get("name", "")).lower()
                    if iname in existing_names:
                        continue
                    merged.append(item)
                merged = sorted(merged, key=lambda e: str(e.get("name", "")).lower())
                if self._write_player_list(whitelist_path, merged):
                    if process and process.is_running:
                        process.send_command(f"whitelist add {name}")
                    self._refresh_player_lists()
                    self._toast(f"Restored {name} to whitelist")

            self._toast(
                f"Removed {name} from whitelist",
                button_label="Undo",
                on_button=undo_remove,
                timeout=6,
            )

    def _remove_banned_player(self, name: str):
        _, banned_path = self._player_list_paths()
        if not banned_path:
            return

        entries = self._read_player_list(banned_path)
        removed = [e for e in entries if str(e.get("name", "")).lower() == name.lower()]
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

            def undo_remove():
                current = self._read_player_list(banned_path)
                existing_names = {str(e.get("name", "")).lower() for e in current}
                merged = list(current)
                for item in removed:
                    iname = str(item.get("name", "")).lower()
                    if iname in existing_names:
                        continue
                    merged.append(item)
                merged = sorted(merged, key=lambda e: str(e.get("name", "")).lower())
                if self._write_player_list(banned_path, merged):
                    if process and process.is_running:
                        process.send_command(f"ban {name}")
                    self._refresh_player_lists()
                    self._toast(f"Restored ban for {name}")

            self._toast(
                f"Unbanned {name}",
                button_label="Undo",
                on_button=undo_remove,
                timeout=6,
            )

