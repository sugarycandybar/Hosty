"""
FilesView — folders, worlds, backups, and Modrinth integration (per selected server).
"""
from __future__ import annotations

import json
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk, GdkPixbuf

from hosty.shared.backend.server_manager import ServerManager, ServerInfo



from ..utils import *

class PlayersMixin:
    def _push_players_page(self, *_args) -> None:
        page = Adw.NavigationPage(title="Players", child=self._build_players_page())
        self._nav.push(page)

    def _build_players_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        actions = Adw.PreferencesGroup(
            title="Manage Players",
            description="Add names to whitelist or ban list",
        )
        self._players_name_row = Adw.EntryRow(title="Player name")
        self._players_name_row.set_show_apply_button(False)
        actions.add(self._players_name_row)

        add_row = Adw.ActionRow(
            title="Add to whitelist"
        )
        add_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        add_row.set_activatable(True)
        add_row.connect("activated", self._on_add_whitelist)
        actions.add(add_row)

        ban_row = Adw.ActionRow(
            title="Ban player",
        )
        ban_row.add_prefix(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        ban_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        ban_row.set_activatable(True)
        ban_row.connect("activated", self._on_add_banned)
        actions.add(ban_row)
        page.add(actions)

        self._whitelist_group = Adw.PreferencesGroup(title="Whitelist")
        page.add(self._whitelist_group)

        self._banned_group = Adw.PreferencesGroup(title="Banned Players")
        page.add(self._banned_group)

        self._refresh_player_lists()

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(page)
        return self._build_subpage_shell("Players", sw)

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

    def _refresh_player_lists(self) -> None:
        if not self._whitelist_group or not self._banned_group:
            return

        self._clear_group_rows(self._whitelist_group, self._whitelist_rows)
        self._clear_group_rows(self._banned_group, self._banned_rows)

        whitelist_path, banned_path = self._player_list_paths()
        if not whitelist_path or not banned_path:
            self._whitelist_rows.append(self._add_info_row(self._whitelist_group, "No server selected"))
            self._banned_rows.append(self._add_info_row(self._banned_group, "No server selected"))
            return

        whitelist = self._read_player_list(whitelist_path)
        banned = self._read_player_list(banned_path)

        if not whitelist:
            self._whitelist_rows.append(self._add_info_row(self._whitelist_group, "No whitelisted players"))
        else:
            for entry in whitelist:
                name = str(entry.get("name", "")).strip()
                row = Adw.ActionRow(title=name)
                row.set_subtitle(str(entry.get("uuid", "Unknown UUID")))
                row.set_activatable(False)
                remove_btn = self._icon_button(
                    "user-trash-symbolic",
                    "Remove from whitelist",
                    lambda *_p, n=name: self._remove_whitelist_player(n),
                    destructive=True,
                )
                row.add_suffix(remove_btn)
                self._whitelist_group.add(row)
                self._whitelist_rows.append(row)

        if not banned:
            self._banned_rows.append(self._add_info_row(self._banned_group, "No banned players"))
        else:
            for entry in banned:
                name = str(entry.get("name", "")).strip()
                reason = str(entry.get("reason", "Banned")).strip()
                row = Adw.ActionRow(title=name)
                row.set_subtitle(reason)
                row.set_activatable(False)
                remove_btn = self._icon_button(
                    "user-trash-symbolic",
                    "Pardon player",
                    lambda *_p, n=name: self._remove_banned_player(n),
                    destructive=True,
                )
                row.add_suffix(remove_btn)
                self._banned_group.add(row)
                self._banned_rows.append(row)

    def _entered_player_name(self) -> str:
        if not self._players_name_row:
            return ""
        return self._players_name_row.get_text().strip()

    def _dash_uuid(self, value: str) -> str:
        raw = value.strip()
        if len(raw) != 32:
            return raw
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"

    def _resolve_profile(self, name: str) -> tuple[str, str]:
        """Best-effort Mojang profile lookup; returns (resolved_name, uuid)."""
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

    def _on_add_whitelist(self, *_args) -> None:
        self._add_player(list_type="whitelist")

    def _on_add_banned(self, *_args) -> None:
        self._add_player(list_type="banned")

    def _add_player(self, list_type: str) -> None:
        name = self._entered_player_name()
        if not name:
            self._alert("Missing player name", "Enter a player name first.")
            return

        whitelist_path, banned_path = self._player_list_paths()
        path = whitelist_path if list_type == "whitelist" else banned_path
        if not path:
            self._alert("No server selected", "Select a server first.")
            return

        process = self._process()
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
                    if self._players_name_row:
                        self._players_name_row.set_text("")
                else:
                    self._alert("Could not save", "Failed to write player list file.")

            GLib.idle_add(ui_apply)

        threading.Thread(target=worker, daemon=True).start()

    def _remove_whitelist_player(self, name: str) -> None:
        whitelist_path, _ = self._player_list_paths()
        if not whitelist_path:
            return

        entries = self._read_player_list(whitelist_path)
        new_entries = [e for e in entries if str(e.get("name", "")).lower() != name.lower()]
        if len(new_entries) == len(entries):
            return

        if self._write_player_list(whitelist_path, new_entries):
            process = self._process()
            if process and process.is_running:
                process.send_command(f"whitelist remove {name}")
            self._refresh_player_lists()
            self._toast(f"Removed {name} from whitelist")

    def _remove_banned_player(self, name: str) -> None:
        _, banned_path = self._player_list_paths()
        if not banned_path:
            return

        entries = self._read_player_list(banned_path)
        new_entries = [e for e in entries if str(e.get("name", "")).lower() != name.lower()]
        if len(new_entries) == len(entries):
            return

        if self._write_player_list(banned_path, new_entries):
            process = self._process()
            if process and process.is_running:
                process.send_command(f"pardon {name}")
            self._refresh_player_lists()
            self._toast(f"Unbanned {name}")

