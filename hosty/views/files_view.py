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

from hosty.backend.server_manager import ServerManager, ServerInfo


def _open_uri(uri: str) -> bool:
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True
    except Exception:
        pass

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


def _open_path(path: Path) -> bool:
    p = path.resolve()
    target = p.parent if p.is_file() else p

    if sys.platform == "win32":
        try:
            os.startfile(str(target))
            return True
        except Exception:
            pass

    return _open_uri(target.as_uri())


def _world_dirs(server_root: Path) -> list[Path]:
    out = []
    if not server_root.is_dir():
        return out
    for item in server_root.iterdir():
        if item.is_dir() and (item / "level.dat").exists():
            out.append(item)
    return sorted(out, key=lambda p: p.name.lower())


def _world_dimension_dirs(world_dir: Path) -> list[tuple[str, Path]]:
    dims: list[tuple[str, Path]] = []

    # Main world root typically contains overworld data.
    if (world_dir / "region").is_dir() or (world_dir / "entities").is_dir():
        dims.append(("Overworld", world_dir))

    legacy_map = [
        ("Nether", world_dir / "DIM-1"),
        ("End", world_dir / "DIM1"),
    ]
    for label, path in legacy_map:
        if path.is_dir():
            dims.append((label, path))

    modern_root = world_dir / "dimensions"
    if modern_root.is_dir():
        for namespace_dir in sorted([p for p in modern_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            for dim_dir in sorted([p for p in namespace_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
                ns = namespace_dir.name
                dim_name = dim_dir.name.replace("_", " ").title()
                label = dim_name if ns == "minecraft" else f"{ns}:{dim_dir.name}"
                dims.append((label, dim_dir))

    # Keep insertion order while de-duplicating by path.
    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for label, path in dims:
        rp = path.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append((label, path))

    return unique


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    val = float(size)
    for u in units:
        if val < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(val)} {u}"
            return f"{val:.1f} {u}"
        val /= 1024.0
    return f"{int(size)} B"


def _format_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _format_compact_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _is_descendant_of(widget: Gtk.Widget, ancestor: Gtk.Widget) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = current.get_parent()
    return False


class FilesView(Gtk.Box):
    """Browse files for the currently selected server only."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._server_info: Optional[ServerInfo] = None
        self._server_manager: Optional[ServerManager] = None
        self._root_page: Optional[Adw.NavigationPage] = None

        self._worlds_group: Optional[Adw.PreferencesGroup] = None
        self._mods_group: Optional[Adw.PreferencesGroup] = None
        self._check_updates_row: Optional[Adw.ActionRow] = None
        self._mods_update_busy = False
        self._modpack_version_enrich_busy = False
        self._active_mod_operation_tokens: dict[str, str] = {}
        self._mod_operation_lock = threading.Lock()
        self._players_group: Optional[Adw.PreferencesGroup] = None
        self._world_rows: list[Gtk.Widget] = []
        self._mod_rows: list[Gtk.Widget] = []

        self._backups_group: Optional[Adw.PreferencesGroup] = None
        self._backup_rows: list[Gtk.Widget] = []
        self._backup_busy = False
        self._create_backup_row: Optional[Adw.ActionRow] = None
        self._backup_spinner: Optional[Gtk.Spinner] = None

        self._players_name_row: Optional[Adw.EntryRow] = None
        self._whitelist_group: Optional[Adw.PreferencesGroup] = None
        self._banned_group: Optional[Adw.PreferencesGroup] = None
        self._whitelist_rows: list[Gtk.Widget] = []
        self._banned_rows: list[Gtk.Widget] = []

        self._nav = Adw.NavigationView()
        self._nav.set_vexpand(True)
        self.append(self._nav)

        root_content = self._build_root_content()
        self._root_page = Adw.NavigationPage(title="Files", child=root_content)
        try:
            self._root_page.set_tag("hosty-files-root")
        except Exception:
            pass
        self._nav.push(self._root_page)

    def _build_root_content(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        self._worlds_group = Adw.PreferencesGroup(title="Worlds")
        open_server_row = Adw.ActionRow(title="Open server folder")
        open_server_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        open_server_row.set_activatable(True)
        open_server_row.connect("activated", self._on_open_server_folder)
        self._worlds_group.add(open_server_row)

        backups_row = Adw.ActionRow(
            title="Backups",
            subtitle="Create, restore, and manage backup archives",
        )
        backups_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        backups_row.set_activatable(True)
        backups_row.connect("activated", self._push_backups_page)
        self._worlds_group.add(backups_row)
        page.add(self._worlds_group)

        self._mods_group = Adw.PreferencesGroup(title="Mods")
        open_mods_row = Adw.ActionRow(title="Open mods folder")
        open_mods_row.add_prefix(Gtk.Image.new_from_icon_name("application-x-addon-symbolic"))
        open_mods_row.set_activatable(True)
        open_mods_row.connect("activated", self._on_open_mods_folder)
        self._mods_group.add(open_mods_row)

        modrinth_row = Adw.ActionRow(
            title="Modrinth",
            subtitle="Discover and install compatible mods and modpacks",
        )
        modrinth_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        modrinth_row.set_activatable(True)
        modrinth_row.connect("activated", self._push_modrinth_page)
        self._mods_group.add(modrinth_row)

        check_updates_row = Adw.ActionRow(
            title="Check for mod updates",
        )
        check_updates_row.add_prefix(Gtk.Image.new_from_icon_name("software-update-available-symbolic"))
        check_updates_row.set_activatable(True)
        check_updates_row.connect("activated", self._on_check_mod_updates)
        self._mods_group.add(check_updates_row)
        self._check_updates_row = check_updates_row
        page.add(self._mods_group)

        self._players_group = Adw.PreferencesGroup(title="Players")

        scroll.set_child(page)
        return scroll

    def set_server(self, server_info: ServerInfo, server_manager: ServerManager):
        self._pop_to_root()
        self._server_info = server_info
        self._server_manager = server_manager
        self._rebuild_lists()

    def _pop_to_root(self) -> None:
        if not self._root_page:
            return
        try:
            self._nav.pop_to_tag("hosty-files-root")
        except Exception:
            for _ in range(24):
                if self._nav.get_visible_page() == self._root_page:
                    break
                self._nav.pop()

    def _server_dir(self) -> Optional[Path]:
        if not self._server_info:
            return None
        return Path(self._server_info.server_dir)

    def _begin_mod_operation(self) -> Optional[str]:
        if not self._server_info or not self._server_manager:
            return None
        server_id = str(self._server_info.id)
        if not server_id:
            return None
        token = uuid.uuid4().hex
        with self._mod_operation_lock:
            self._active_mod_operation_tokens[token] = server_id
        self._server_manager.begin_mod_operation(server_id)
        return token

    def _end_mod_operation(self, token: Optional[str]) -> None:
        if not token:
            return
        server_id = None
        with self._mod_operation_lock:
            server_id = self._active_mod_operation_tokens.pop(token, None)
        if server_id and self._server_manager:
            self._server_manager.end_mod_operation(server_id)

    def _mod_dependency_state_path(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-mod-dependencies.json"

    def _modpack_state_path(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-modpacks.json"

    def _individual_mod_state_path(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-mod-installs.json"

    def _read_modpack_state(self) -> dict:
        path = self._modpack_state_path()
        if not path or not path.exists():
            return {"installed_projects": {}}

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                projects = raw.get("installed_projects")
                if isinstance(projects, dict):
                    normalized: dict[str, dict[str, Any]] = {}
                    for project_id, value in projects.items():
                        pid = str(project_id).strip()
                        if not pid:
                            continue

                        item = value
                        # Recover from older buggy state where a dict was stringified.
                        if isinstance(item, str):
                            text = item.strip()
                            if text.startswith("{") and text.endswith("}"):
                                try:
                                    recovered = ast.literal_eval(text)
                                    if isinstance(recovered, dict):
                                        item = recovered
                                except Exception:
                                    pass

                        if isinstance(item, dict):
                            version_id = str(item.get("version_id", "")).strip()
                            version_number = str(item.get("version_number", "")).strip()
                            title = str(item.get("title", "")).strip()
                            mods_raw = item.get("mods") if isinstance(item.get("mods"), list) else []
                            mods = sorted(
                                {
                                    str(Path(str(m)).name).strip().lower()
                                    for m in mods_raw
                                    if str(m).strip().lower().endswith(".jar")
                                }
                            )
                            normalized[pid] = {
                                "version_id": version_id,
                                "version_number": version_number,
                                "title": title,
                                "mods": mods,
                            }
                        else:
                            # Legacy minimal state: project -> version_id
                            normalized[pid] = {
                                "version_id": str(item).strip(),
                                "version_number": "",
                                "title": "",
                                "mods": [],
                            }
                    return {"installed_projects": normalized}
        except Exception:
            pass

        return {"installed_projects": {}}

    def _modpack_entries(self) -> dict[str, dict[str, Any]]:
        state = self._read_modpack_state()
        projects = state.get("installed_projects", {})
        if not isinstance(projects, dict):
            return {}

        out: dict[str, dict[str, Any]] = {}
        for project_id, value in projects.items():
            pid = str(project_id).strip()
            if not pid:
                continue

            if isinstance(value, dict):
                version_id = str(value.get("version_id", "")).strip()
                version_number = str(value.get("version_number", "")).strip()
                title = str(value.get("title", "")).strip()
                mods_raw = value.get("mods") if isinstance(value.get("mods"), list) else []
                mods = sorted(
                    {
                        str(Path(str(m)).name).strip().lower()
                        for m in mods_raw
                        if str(m).strip().lower().endswith(".jar")
                    }
                )
            else:
                version_id = str(value).strip()
                version_number = ""
                title = ""
                mods = []

            out[pid] = {
                "version_id": version_id,
                "version_number": version_number,
                "title": title,
                "mods": mods,
            }

        return out

    def _modpack_managed_mod_map(self) -> dict[str, list[str]]:
        managed: dict[str, list[str]] = {}
        for project_id, entry in self._modpack_entries().items():
            label = str(entry.get("title", "")).strip() or project_id
            for mod_name in entry.get("mods", []):
                key = str(mod_name).strip().lower()
                if not key:
                    continue
                names = managed.setdefault(key, [])
                if label not in names:
                    names.append(label)
        return managed

    def _read_individual_mod_state(self) -> dict:
        path = self._individual_mod_state_path()
        if not path or not path.exists():
            return {"mods": {}}

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return {"mods": {}}

            mods_raw = raw.get("mods") if isinstance(raw.get("mods"), dict) else {}
            cleaned: dict[str, dict[str, str]] = {}
            for project_id, item in mods_raw.items():
                pid = str(project_id).strip()
                if not pid or not isinstance(item, dict):
                    continue

                title = str(item.get("title", "")).strip()
                version_id = str(item.get("version_id", "")).strip()
                filename = str(item.get("filename", "")).strip()
                if not filename:
                    continue

                cleaned[pid] = {
                    "title": title,
                    "version_id": version_id,
                    "filename": filename,
                }

            return {"mods": cleaned}
        except Exception:
            return {"mods": {}}

    def _write_individual_mod_state(self, state: dict) -> bool:
        path = self._individual_mod_state_path()
        if not path:
            return False

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception:
            return False

    def _record_individual_mod_install(
        self,
        project_id: str,
        title: str,
        version_id: str,
        filename: str,
    ) -> None:
        pid = str(project_id).strip()
        if not pid:
            return

        state = self._read_individual_mod_state()
        mods = state.setdefault("mods", {})
        mods[pid] = {
            "title": str(title or "").strip(),
            "version_id": str(version_id or "").strip(),
            "filename": str(filename or "").strip(),
        }
        self._write_individual_mod_state(state)

    def _write_modpack_state(self, state: dict) -> bool:
        path = self._modpack_state_path()
        if not path:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception:
            return False

    def _record_modpack_install(
        self,
        project_id: str,
        version_id: str,
        version_number: str = "",
        title: str = "",
        mod_files: Optional[list[str]] = None,
    ) -> None:
        pid = str(project_id).strip()
        if not pid:
            return
        state = self._read_modpack_state()
        projects = state.setdefault("installed_projects", {})
        normalized_mods = sorted(
            {
                str(Path(str(m)).name).strip().lower()
                for m in (mod_files or [])
                if str(m).strip().lower().endswith(".jar")
            }
        )
        projects[pid] = {
            "version_id": str(version_id).strip(),
            "version_number": str(version_number or "").strip(),
            "title": str(title or "").strip(),
            "mods": normalized_mods,
        }
        self._write_modpack_state(state)

    def _is_modpack_installed(self, project_id: str) -> bool:
        pid = str(project_id).strip()
        if not pid:
            return False
        entries = self._modpack_entries()
        return pid in entries

    def _find_mod_jar_path(self, mods_dir: Path, filename: str) -> Optional[Path]:
        """Resolve a jar path by filename, with case-insensitive fallback."""
        name = str(filename).strip()
        if not name:
            return None

        direct = mods_dir / name
        if direct.exists():
            return direct

        name_l = name.lower()
        for jar in mods_dir.glob("*.jar"):
            if jar.name.lower() == name_l:
                return jar
        return None

    def _remove_mod_from_mod_states(self, removed_filename: str) -> None:
        key = str(removed_filename).strip().lower()
        if not key:
            return

        self._remove_mod_from_dependency_state(removed_filename)

        # Remove from standalone install tracking.
        standalone = self._read_individual_mod_state()
        mods = dict(standalone.get("mods", {}))
        kept = {}
        for project_id, meta in mods.items():
            fname = str((meta or {}).get("filename", "")).strip().lower()
            if fname == key:
                continue
            kept[project_id] = meta
        self._write_individual_mod_state({"mods": kept})

        # Remove from any modpack-managed mod list if manually deleted.
        entries = self._modpack_entries()
        projects_payload: dict[str, dict[str, Any]] = {}
        for project_id, entry in entries.items():
            mods = [m for m in entry.get("mods", []) if str(m).strip().lower() != key]
            projects_payload[project_id] = {
                "version_id": str(entry.get("version_id", "")).strip(),
                "version_number": str(entry.get("version_number", "")).strip(),
                "title": str(entry.get("title", "")).strip(),
                "mods": mods,
            }
        self._write_modpack_state({"installed_projects": projects_payload})

    def _ensure_modpack_version_numbers_async(self) -> None:
        if self._modpack_version_enrich_busy:
            return

        entries = self._modpack_entries()
        missing = [
            (project_id, entry)
            for project_id, entry in entries.items()
            if not str(entry.get("version_number", "")).strip()
            and str(entry.get("version_id", "")).strip()
        ]
        if not missing:
            return

        self._modpack_version_enrich_busy = True

        def worker():
            from hosty.backend import modrinth_client

            latest_entries = self._modpack_entries()
            changed = False
            payload: dict[str, dict[str, Any]] = {}

            for project_id, entry in latest_entries.items():
                version_id = str(entry.get("version_id", "")).strip()
                version_number = str(entry.get("version_number", "")).strip()
                if not version_number and version_id:
                    raw = modrinth_client.get_version(version_id)
                    if isinstance(raw, dict):
                        version_number = (
                            str(raw.get("version_number", "")).strip()
                            or str(raw.get("name", "")).strip()
                        )
                    if version_number:
                        changed = True

                payload[project_id] = {
                    "version_id": version_id,
                    "version_number": version_number,
                    "title": str(entry.get("title", "")).strip(),
                    "mods": [
                        str(m).strip().lower()
                        for m in (entry.get("mods") or [])
                        if str(m).strip().lower().endswith(".jar")
                    ],
                }

            def finish_ui():
                self._modpack_version_enrich_busy = False
                if changed:
                    self._write_modpack_state({"installed_projects": payload})
                    self._rebuild_lists()
                return False

            GLib.idle_add(finish_ui)

        threading.Thread(target=worker, daemon=True).start()

    def _read_mod_dependency_state(self) -> dict:
        path = self._mod_dependency_state_path()
        if not path or not path.exists():
            return {"required_by": {}}

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                req = raw.get("required_by")
                if isinstance(req, dict):
                    cleaned = {}
                    for dep_name, parents in req.items():
                        dep_key = str(dep_name).strip().lower()
                        if not dep_key:
                            continue
                        if not isinstance(parents, list):
                            continue
                        parent_keys = sorted(
                            {
                                str(p).strip().lower()
                                for p in parents
                                if str(p).strip()
                            }
                        )
                        if parent_keys:
                            cleaned[dep_key] = parent_keys
                    return {"required_by": cleaned}
        except Exception:
            pass

        return {"required_by": {}}

    def _write_mod_dependency_state(self, state: dict) -> bool:
        path = self._mod_dependency_state_path()
        if not path:
            return False

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception:
            return False

    def _record_dependency_installs(self, parent_filename: str, dep_versions: list) -> None:
        parent_key = str(parent_filename).strip().lower()
        if not parent_key or not dep_versions:
            return

        state = self._read_mod_dependency_state()
        req = state.setdefault("required_by", {})
        for dep in dep_versions:
            dep_key = str(getattr(dep, "filename", "")).strip().lower()
            if not dep_key or dep_key == parent_key:
                continue
            parents = set(req.get(dep_key, []))
            parents.add(parent_key)
            req[dep_key] = sorted(parents)

        self._write_mod_dependency_state(state)

    def _remove_mod_from_dependency_state(self, removed_filename: str) -> None:
        removed_key = str(removed_filename).strip().lower()
        if not removed_key:
            return

        state = self._read_mod_dependency_state()
        req = dict(state.get("required_by", {}))
        req.pop(removed_key, None)

        new_req = {}
        for dep_key, parents in req.items():
            filtered = [p for p in parents if p != removed_key]
            if filtered:
                new_req[dep_key] = filtered

        self._write_mod_dependency_state({"required_by": new_req})

    def _dependency_dependents(self, filename: str) -> list[str]:
        key = str(filename).strip().lower()
        if not key:
            return []
        state = self._read_mod_dependency_state()
        req = state.get("required_by", {})
        parents = list(req.get(key, []))

        root = self._server_dir()
        if not root:
            return parents
        mods_dir = root / "mods"
        installed = {p.name.lower() for p in mods_dir.glob("*.jar")} if mods_dir.is_dir() else set()
        return [p for p in parents if p in installed]

    def _process(self):
        if not self._server_info or not self._server_manager:
            return None
        return self._server_manager.get_process(self._server_info.id)

    def _is_running(self) -> bool:
        p = self._process()
        return p is not None and p.is_running

    def _clear_group_rows(self, group: Adw.PreferencesGroup, rows: list[Gtk.Widget]) -> None:
        for row in list(rows):
            try:
                if _is_descendant_of(row, group):
                    group.remove(row)
            except Exception:
                pass
        rows.clear()

    def _rebuild_lists(self) -> None:
        if not self._worlds_group or not self._mods_group:
            return

        self._ensure_modpack_version_numbers_async()

        self._clear_group_rows(self._worlds_group, self._world_rows)
        self._clear_group_rows(self._mods_group, self._mod_rows)

        root = self._server_dir()
        if not root or not root.is_dir():
            self._world_rows.append(self._add_info_row(self._worlds_group, "No server folder"))
            self._mod_rows.append(self._add_info_row(self._mods_group, "No server folder"))
            return

        worlds = _world_dirs(root)
        if not worlds:
            self._world_rows.append(self._add_info_row(self._worlds_group, "No worlds yet"))
        else:
            for w in worlds:
                row = self._make_world_row(w)
                self._worlds_group.add(row)
                self._world_rows.append(row)

        mods_dir = root / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        jars = sorted(mods_dir.glob("*.jar"), key=lambda p: p.name.lower())
        entries = self._modpack_entries()
        managed_set = set(self._modpack_managed_mod_map().keys())

        for project_id, entry in sorted(
            entries.items(),
            key=lambda item: (str(item[1].get("title", "")).strip() or item[0]).lower(),
        ):
            row = self._make_modpack_row(project_id, entry)
            self._mods_group.add(row)
            self._mod_rows.append(row)

        standalone_jars = [jar for jar in jars if jar.name.lower() not in managed_set]

        if not standalone_jars and not entries:
            self._mod_rows.append(self._add_info_row(self._mods_group, "No mods installed"))
            return

        for jar in standalone_jars:
            row = self._make_mod_row(jar)
            self._mods_group.add(row)
            self._mod_rows.append(row)

    def _add_info_row(self, group: Adw.PreferencesGroup, title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        row.set_activatable(False)
        group.add(row)
        return row

    def _icon_button(
        self,
        icon_name: str,
        tooltip: str,
        handler,
        destructive: bool = False,
    ) -> Gtk.Button:
        b = Gtk.Button(icon_name=icon_name)
        b.add_css_class("flat")
        if destructive:
            b.add_css_class("destructive-action")
        b.set_tooltip_text(tooltip)
        b.connect("clicked", handler)
        return b

    def _make_world_row(self, path: Path) -> Adw.ActionRow:
        dims = _world_dimension_dirs(path)
        row = Adw.ActionRow(title=path.name)
        dim_count = len(dims)
        row.set_subtitle(f"{dim_count} dimension{'s' if dim_count != 1 else ''}")
        row.set_activatable(True)
        row.connect("activated", lambda *_p, p=path: self._open_world_dialog(p))
        open_btn = self._icon_button(
            "folder-open-symbolic",
            "Open world folder",
            lambda *_p, p=path: self._open_target(p),
        )
        del_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete world",
            lambda *_p, p=path, n=path.name: self._confirm_delete_world(p, n),
            destructive=True,
        )
        row.add_suffix(open_btn)
        row.add_suffix(del_btn)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    def _open_world_dialog(self, world_path: Path) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(world_path.name)
        dialog.set_body("Manage dimensions.")
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(6)
        content.set_margin_bottom(6)

        group = Adw.PreferencesGroup(title="Dimensions")
        dims = _world_dimension_dirs(world_path)
        if not dims:
            none_row = Adw.ActionRow(title="No dimension folders found")
            none_row.set_activatable(False)
            group.add(none_row)
        else:
            world_root = world_path.resolve()
            for label, dim_path in dims:
                row = Adw.ActionRow(title=label)
                row.set_activatable(False)

                open_dim_btn = self._icon_button(
                    "folder-open-symbolic",
                    "Open dimension folder",
                    lambda *_p, p=dim_path: self._open_target(p),
                )
                row.add_suffix(open_dim_btn)

                # Deleting the world root dimension would remove the full world.
                if dim_path.resolve() != world_root:
                    delete_btn = self._icon_button(
                        "user-trash-symbolic",
                        "Delete dimension",
                        lambda *_p, w=world_path, p=dim_path, n=label: self._confirm_delete_dimension(w, p, n),
                        destructive=True,
                    )
                    row.add_suffix(delete_btn)

                group.add(row)

        content.append(group)
        dialog.set_extra_child(content)
        dialog.present(self.get_root())

    def _confirm_delete_dimension(self, world_path: Path, dim_path: Path, name: str):
        if self._is_running():
            self._alert("Server is running", "Stop the server before deleting a dimension.")
            return

        if dim_path.resolve() == world_path.resolve():
            self._alert("Cannot delete world root", "Use Delete world to remove the entire world.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete dimension?")
        dialog.set_body(f"Delete dimension “{name}”? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._soft_delete_with_undo(
                    dim_path,
                    f"dimension \"{name}\"",
                    on_refresh=self._rebuild_lists,
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _make_mod_row(self, jar: Path) -> Adw.ActionRow:
        row = Adw.ActionRow(title=jar.name)
        subtitle = _format_size(jar.stat().st_size)
        dependents = self._dependency_dependents(jar.name)
        if dependents:
            subtitle = f"{subtitle} · Dependency"
        row.set_subtitle(subtitle)
        row.set_activatable(False)
        del_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete mod",
            lambda *_p, p=jar, n=jar.name: self._confirm_delete_mod(p, n),
            destructive=True,
        )
        row.add_suffix(del_btn)
        return row

    def _make_modpack_row(self, project_id: str, entry: dict[str, Any]) -> Adw.ActionRow:
        title = str(entry.get("title", "")).strip() or project_id
        mods = [str(m).strip() for m in (entry.get("mods") or []) if str(m).strip()]
        version_id = str(entry.get("version_id", "")).strip()
        version_number = str(entry.get("version_number", "")).strip()

        row = Adw.ActionRow(title=title)
        subtitle_bits = [f"{len(mods)} managed mods"]
        if version_number:
            subtitle_bits.append(f"version {version_number}")
        elif version_id:
            subtitle_bits.append(f"version {version_id[:8]}")
        row.set_subtitle(" · ".join(subtitle_bits))
        row.set_activatable(False)

        view_btn = self._icon_button(
            "view-list-symbolic",
            "View managed mods",
            lambda *_p, t=title, m=mods: self._show_modpack_mods_dialog(t, m),
        )
        open_btn = self._icon_button(
            "web-browser-symbolic",
            "Open modpack page",
            lambda *_p, pid=project_id: _open_uri(f"https://modrinth.com/modpack/{pid}"),
        )
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete modpack",
            lambda *_p, pid=project_id, t=title: self._confirm_delete_modpack(pid, t),
            destructive=True,
        )
        row.add_suffix(view_btn)
        row.add_suffix(open_btn)
        row.add_suffix(delete_btn)
        return row

    def _confirm_delete_modpack(self, project_id: str, title: str) -> None:
        if self._is_running():
            self._alert("Server is running", "Stop the server before deleting a modpack.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete modpack?")
        dialog.set_body(
            f"Remove \"{title}\" and delete its managed mod files from this server?"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._delete_modpack(project_id, title)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _delete_modpack(self, project_id: str, title: str) -> None:
        entry = self._modpack_entries().get(project_id)
        if not entry:
            return

        root = self._server_dir()
        if not root:
            self._alert("No server selected", "Select a server before deleting a modpack.")
            return

        mods_dir = root / "mods"
        removed_count = 0
        for mod_name in [str(m).strip() for m in (entry.get("mods") or []) if str(m).strip()]:
            target = self._find_mod_jar_path(mods_dir, mod_name)
            if target and target.exists():
                target.unlink(missing_ok=True)
                removed_count += 1
            self._remove_mod_from_mod_states(mod_name)

        state = self._read_modpack_state()
        projects = state.get("installed_projects", {})
        if isinstance(projects, dict):
            projects.pop(project_id, None)
            self._write_modpack_state({"installed_projects": projects})

        self._rebuild_lists()
        self._toast(f"Deleted {title} ({removed_count} mod files removed)")

    def _show_modpack_mods_dialog(self, modpack_title: str, mods: list[str]) -> None:
        d = Adw.AlertDialog()
        d.set_heading(modpack_title)
        cleaned = []
        for item in mods:
            name = str(item).strip()
            if name.startswith("- "):
                name = name[2:].strip()
            if name:
                cleaned.append(name)

        if not cleaned:
            d.set_body("No tracked mod files for this modpack yet.")
            d.add_response("ok", "OK")
            d.present(self.get_root())
            return

        cleaned = sorted(set(cleaned), key=str.lower)
        d.set_body(f"{len(cleaned)} managed mods")

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for name in cleaned:
            row = Adw.ActionRow(title=name)
            row.set_activatable(False)
            row.add_prefix(Gtk.Image.new_from_icon_name("application-x-addon-symbolic"))
            listbox.append(row)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(360)
        sw.set_child(listbox)
        d.set_extra_child(sw)

        d.add_response("ok", "OK")
        d.present(self.get_root())

    def _set_mod_update_row_subtitle(self, subtitle: str) -> None:
        if self._check_updates_row:
            self._check_updates_row.set_subtitle(subtitle)

    def _on_check_mod_updates(self, *_args) -> None:
        if self._mods_update_busy:
            self._toast("Mod update check already running")
            return
        if self._is_running():
            self._alert("Server is running", "Stop the server before checking for mod updates.")
            return
        if not self._server_info or not self._server_info.mc_version:
            self._alert("Unknown version", "Could not determine Minecraft version for this server.")
            return

        self._mods_update_busy = True
        self._set_mod_update_row_subtitle("Checking for updates...")

        def worker():
            from hosty.backend import modrinth_client

            mc_version = self._server_info.mc_version if self._server_info else ""
            modpack_entries = self._modpack_entries()
            managed_mods = set(self._modpack_managed_mod_map().keys())
            individual_state = self._read_individual_mod_state().get("mods", {})

            modpack_updates = []
            for project_id, entry in modpack_entries.items():
                current_version = str(entry.get("version_id", "")).strip()
                current_version_number = str(entry.get("version_number", "")).strip()
                versions = modrinth_client.get_project_versions(project_id)
                compatible = [v for v in versions if mc_version in (v.game_versions or [])]
                if not compatible:
                    continue
                latest = compatible[0]

                latest_id = str(latest.version_id).strip()
                latest_number = str(latest.version_number).strip()
                same_id = current_version and (latest_id == current_version)
                same_number = current_version_number and (latest_number == current_version_number)
                newer = None if (same_id or same_number) else latest
                if newer:
                    modpack_updates.append((project_id, entry, newer))

            standalone_updates = []
            blocked = 0
            for project_id, meta in individual_state.items():
                current_version = str((meta or {}).get("version_id", "")).strip()
                latest = modrinth_client.find_compatible_version(
                    project_id,
                    mc_version,
                    loader="fabric",
                )
                if not latest:
                    continue
                if str(latest.version_id).strip() == current_version:
                    continue

                deps = modrinth_client.resolve_required_dependencies(
                    latest.version_id,
                    mc_version,
                    loader="fabric",
                )
                dep_hits_modpack = any(str(dep.filename).strip().lower() in managed_mods for dep in deps)
                if dep_hits_modpack:
                    blocked += 1
                    continue

                standalone_updates.append((project_id, meta, latest, deps))

            def show_result():
                total_updates = len(modpack_updates) + len(standalone_updates)
                if total_updates == 0:
                    self._mods_update_busy = False
                    self._set_mod_update_row_subtitle("Update check complete")
                    if blocked > 0:
                        self._toast(f"No safe updates found ({blocked} blocked by modpack-managed dependencies)")
                    else:
                        self._toast("All tracked mods are up to date")
                    return False

                lines: list[str] = []
                if modpack_updates:
                    lines.append("Modpacks:")
                    for pid, entry, newer in modpack_updates[:12]:
                        title = str(entry.get("title", "")).strip() or pid
                        vn = str(newer.version_number or newer.version_id)
                        lines.append(f"- {title} -> {vn}")
                    if len(modpack_updates) > 12:
                        lines.append(f"- and {len(modpack_updates) - 12} more modpacks")

                if standalone_updates:
                    if lines:
                        lines.append("")
                    lines.append("Standalone mods:")
                    for pid, meta, newer, _deps in standalone_updates[:14]:
                        title = str((meta or {}).get("title", "")).strip() or pid
                        vn = str(newer.version_number or newer.version_id)
                        lines.append(f"- {title} -> {vn}")
                    if len(standalone_updates) > 14:
                        lines.append(f"- and {len(standalone_updates) - 14} more mods")

                listing = "\n".join(lines)

                dialog = Adw.AlertDialog()
                dialog.set_heading("Install available updates?")
                dialog.set_body(
                    f"Found {len(modpack_updates)} modpack update(s) and "
                    f"{len(standalone_updates)} standalone mod update(s)."
                    + (f"\n\n{blocked} standalone update(s) were skipped because dependencies are managed by a modpack." if blocked else "")
                    + (f"\n\n{listing}" if listing else "")
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("update", "Update")
                dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("update")
                dialog.set_close_response("cancel")

                def on_response(_d, response):
                    if response != "update":
                        self._mods_update_busy = False
                        self._set_mod_update_row_subtitle("Update check complete")
                        return

                    op_token = self._begin_mod_operation()
                    if not op_token:
                        self._mods_update_busy = False
                        self._set_mod_update_row_subtitle("Update check complete")
                        self._alert("No server selected", "Select a server before updating mods.")
                        return

                    self._set_mod_update_row_subtitle("Updating mods...")
                    self._toast(
                        f"Updating {len(modpack_updates)} modpack(s) and {len(standalone_updates)} mod(s)"
                    )
                    threading.Thread(
                        target=self._apply_mod_updates,
                        args=(modpack_updates, standalone_updates, op_token),
                        daemon=True,
                    ).start()

                dialog.connect("response", on_response)
                dialog.present(self.get_root())
                return False

            GLib.idle_add(show_result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_mod_updates(
        self,
        modpack_updates: list,
        standalone_updates: list,
        mod_operation_token: Optional[str] = None,
    ) -> None:
        from hosty.backend import modrinth_client

        root = self._server_dir()
        if not root:
            GLib.idle_add(lambda: self._alert("No server selected", "Select a server to update mods."))
            GLib.idle_add(lambda: self._set_mod_update_row_subtitle("Update check complete"))
            GLib.idle_add(lambda: setattr(self, "_mods_update_busy", False))
            GLib.idle_add(lambda t=mod_operation_token: self._end_mod_operation(t))
            return

        mods_dir = root / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)

        applied = 0
        failed = 0

        # Apply modpack updates first so pack-managed versions remain authoritative.
        for index, (project_id, entry, newer_version) in enumerate(modpack_updates, start=1):
            pack_title = str(entry.get("title", "")).strip() or project_id
            GLib.idle_add(
                lambda i=index, total=len(modpack_updates), t=pack_title: self._set_mod_update_row_subtitle(
                    f"Updating modpack {i}/{total}: {t}"
                )
            )
            try:
                previous_mods = {
                    str(m).strip().lower()
                    for m in (entry.get("mods") or [])
                    if str(m).strip().lower().endswith(".jar")
                }

                def on_progress(done: int, total: int, rel_path: str):
                    GLib.idle_add(
                        lambda d=done, t=total: self._set_mod_update_row_subtitle(
                            f"Updating {pack_title}: {d}/{t}"
                        )
                    )

                result = modrinth_client.install_modpack(
                    newer_version.version_id,
                    root,
                    progress_callback=on_progress,
                )

                new_managed_mods = {
                    str(m).strip().lower()
                    for m in (result.managed_mod_files or [])
                    if str(m).strip().lower().endswith(".jar")
                }

                removed = previous_mods - new_managed_mods
                for name in removed:
                    old_path = self._find_mod_jar_path(mods_dir, name)
                    if old_path and old_path.exists():
                        old_path.unlink(missing_ok=True)
                    self._remove_mod_from_mod_states(name)

                self._record_modpack_install(
                    project_id,
                    newer_version.version_id,
                    version_number=newer_version.version_number,
                    title=pack_title,
                    mod_files=sorted(new_managed_mods),
                )
                applied += 1
            except Exception:
                failed += 1

        managed_mods = set(self._modpack_managed_mod_map().keys())

        # Apply standalone updates, installing required dependencies first.
        for index, (project_id, meta, latest, deps) in enumerate(standalone_updates, start=1):
            mod_title = str((meta or {}).get("title", "")).strip() or project_id
            GLib.idle_add(
                lambda i=index, total=len(standalone_updates), t=mod_title: self._set_mod_update_row_subtitle(
                    f"Updating standalone mod {i}/{total}: {t}"
                )
            )
            try:
                deps_to_install = [
                    dep
                    for dep in deps
                    if str(dep.filename).strip().lower() not in managed_mods
                ]
                for dep in deps_to_install:
                    modrinth_client.download_to(dep.download_url, mods_dir / dep.filename)

                modrinth_client.download_to(latest.download_url, mods_dir / latest.filename)
                old_name = str((meta or {}).get("filename", "")).strip()
                if old_name and old_name.lower() != latest.filename.lower():
                    old_path = self._find_mod_jar_path(mods_dir, old_name)
                    if old_path and old_path.exists():
                        old_path.unlink(missing_ok=True)
                    self._remove_mod_from_mod_states(old_name)

                self._record_individual_mod_install(
                    project_id,
                    mod_title,
                    latest.version_id,
                    latest.filename,
                )
                self._record_dependency_installs(latest.filename, deps_to_install)
                applied += 1
            except Exception:
                failed += 1

        def finish_ui():
            self._mods_update_busy = False
            self._set_mod_update_row_subtitle("Update check complete")
            self._end_mod_operation(mod_operation_token)
            self._rebuild_lists()
            if failed == 0:
                self._toast(f"Applied {applied} update(s)")
            else:
                self._toast(f"Applied {applied} update(s), {failed} failed")
            return False

        GLib.idle_add(finish_ui)

    def _backups_dir(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        d = root / "hosty-backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _build_subpage_shell(self, title: str, content: Gtk.Widget) -> Gtk.Widget:
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(False)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        header.set_title_widget(title_lbl)

        tv.add_top_bar(header)
        tv.set_content(content)
        return tv

    def _push_backups_page(self, *_args) -> None:
        page = Adw.NavigationPage(title="Backups", child=self._build_backups_page())
        self._nav.push(page)

    def _build_backups_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        actions = Adw.PreferencesGroup(title="Actions")
        create_row = Adw.ActionRow(
            title="Create backup now",
            subtitle="Back up world folders only",
        )
        create_row.add_prefix(Gtk.Image.new_from_icon_name("document-save-symbolic"))
        self._backup_spinner = Gtk.Spinner()
        self._backup_spinner.set_spinning(False)
        self._backup_spinner.set_visible(False)
        create_row.add_suffix(self._backup_spinner)
        create_row.set_activatable(True)
        create_row.connect("activated", lambda *_: self._on_create_backup())
        actions.add(create_row)
        self._create_backup_row = create_row

        open_row = Adw.ActionRow(title="Open backups folder")
        open_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        open_row.set_activatable(True)
        open_row.connect("activated", self._on_open_backups_folder)
        actions.add(open_row)
        page.add(actions)

        self._backups_group = Adw.PreferencesGroup(title="Available Backups")
        page.add(self._backups_group)
        self._refresh_backup_list()

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(page)
        return self._build_subpage_shell("Backups", sw)

    def _refresh_backup_list(self) -> None:
        if not self._backups_group:
            return

        self._clear_group_rows(self._backups_group, self._backup_rows)
        bdir = self._backups_dir()
        if not bdir:
            self._backup_rows.append(self._add_info_row(self._backups_group, "No server selected"))
            return

        zips = sorted(bdir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zips:
            self._backup_rows.append(self._add_info_row(self._backups_group, "No backups yet"))
            return

        for zp in zips:
            row = self._make_backup_row(zp)
            self._backups_group.add(row)
            self._backup_rows.append(row)

    def _make_backup_row(self, zp: Path) -> Adw.ActionRow:
        st = zp.stat()
        row = Adw.ActionRow(title=zp.name)
        row.set_subtitle(f"{_format_size(st.st_size)} · {_format_mtime(st.st_mtime)}")
        row.set_activatable(False)

        restore_btn = self._icon_button(
            "document-revert-symbolic",
            "Restore backup",
            lambda *_p, p=zp: self._confirm_restore_backup(p),
        )
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete backup",
            lambda *_p, p=zp: self._confirm_delete_backup(p),
            destructive=True,
        )

        row.add_suffix(restore_btn)
        row.add_suffix(delete_btn)
        return row

    def _on_create_backup(self) -> None:
        if self._backup_busy:
            self._alert("Backup in progress", "Please wait for the current backup task to finish.")
            return
        if self._is_running():
            self._alert("Server is running", "Stop the server before creating a backup.")
            return

        root = self._server_dir()
        bdir = self._backups_dir()
        if not root or not bdir:
            self._alert("No server selected", "Select a server to manage backups.")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        zp = bdir / f"hosty-backup-{stamp}.zip"
        self._backup_busy = True
        if self._backup_spinner:
            self._backup_spinner.set_visible(True)
            self._backup_spinner.start()
        if self._create_backup_row:
            self._create_backup_row.set_subtitle("Creating backup...")

        def worker():
            try:
                worlds = _world_dirs(root)
                if not worlds:
                    raise RuntimeError("No world folder found to back up.")

                with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                    for world_dir in worlds:
                        for item in world_dir.rglob("*"):
                            if not item.is_file():
                                continue
                            arc = item.relative_to(root)
                            zf.write(item, arcname=str(arc).replace("\\", "/"))

                def ui_ok():
                    self._backup_busy = False
                    if self._backup_spinner:
                        self._backup_spinner.stop()
                        self._backup_spinner.set_visible(False)
                    if self._create_backup_row:
                        self._create_backup_row.set_subtitle("Back up world folders only")
                    self._refresh_backup_list()
                    self._toast(f"Saved {zp.name}")

                GLib.idle_add(ui_ok)
            except Exception as e:
                def ui_err():
                    self._backup_busy = False
                    if self._backup_spinner:
                        self._backup_spinner.stop()
                        self._backup_spinner.set_visible(False)
                    if self._create_backup_row:
                        self._create_backup_row.set_subtitle("Back up world folders only")
                    self._alert("Backup failed", str(e))

                GLib.idle_add(ui_err)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_restore_backup(self, zp: Path) -> None:
        if self._is_running():
            self._alert("Server is running", "Stop the server before restoring a backup.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Restore backup?")
        dialog.set_body(
            f"Restore “{zp.name}”?\n\n"
            "This replaces only world folders contained in the backup."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "restore":
                self._restore_backup(zp)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _restore_backup(self, zp: Path) -> None:
        if self._backup_busy:
            self._alert("Backup task active", "Wait for the active backup task to finish.")
            return

        root = self._server_dir()
        bdir = self._backups_dir()
        if not root or not bdir:
            self._alert("No server selected", "Select a server before restoring a backup.")
            return

        self._backup_busy = True

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="hosty-restore-") as td:
                    tmp_root = Path(td).resolve()
                    with zipfile.ZipFile(zp, "r") as zf:
                        for info in zf.infolist():
                            candidate = (tmp_root / info.filename).resolve()
                            if not _is_relative_to(candidate, tmp_root):
                                raise RuntimeError("Backup archive contains invalid paths.")
                        zf.extractall(tmp_root)

                    extracted_worlds = [
                        item for item in tmp_root.iterdir()
                        if item.is_dir() and (item / "level.dat").exists()
                    ]
                    if not extracted_worlds:
                        raise RuntimeError("This backup does not contain any world data.")

                    for item in extracted_worlds:
                        dst = root / item.name
                        if dst.is_dir():
                            shutil.rmtree(dst, ignore_errors=True)
                        shutil.copytree(item, dst, dirs_exist_ok=True)

                def ui_ok():
                    self._backup_busy = False
                    self._rebuild_lists()
                    self._refresh_backup_list()
                    self._toast("Backup restored")

                GLib.idle_add(ui_ok)
            except Exception as e:
                def ui_err():
                    self._backup_busy = False
                    self._alert("Restore failed", str(e))

                GLib.idle_add(ui_err)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_delete_backup(self, zp: Path) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete backup?")
        dialog.set_body(f"Remove “{zp.name}”?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._soft_delete_with_undo(
                    zp,
                    f"backup \"{zp.name}\"",
                    on_refresh=self._refresh_backup_list,
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _push_modrinth_page(self, *_args) -> None:
        page = Adw.NavigationPage(title="Modrinth", child=self._build_modrinth_page())
        self._nav.push(page)

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
            title="Add to whitelist",
            subtitle="Allow this player to join",
        )
        add_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        add_row.set_activatable(True)
        add_row.connect("activated", self._on_add_whitelist)
        actions.add(add_row)

        ban_row = Adw.ActionRow(
            title="Ban player",
            subtitle="Block this player from joining",
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

    def _build_modrinth_page(self) -> Gtk.Widget:
        from hosty.backend import modrinth_client

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(False)

        search_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_header.set_hexpand(True)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_placeholder_text("Search Modrinth…")
        btn = Gtk.Button(label="Search")
        btn.add_css_class("suggested-action")
        search_header.append(entry)
        search_header.append(btn)
        header.set_title_widget(search_header)
        tv.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        outer.set_margin_top(12)
        outer.set_margin_bottom(18)

        mc_ver = self._server_info.mc_version if self._server_info else ""

        controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        project_type_items = [
            ("Mods", "mod"),
            ("Modpacks", "modpack"),
        ]
        type_dd = Gtk.DropDown.new_from_strings([x[0] for x in project_type_items])
        type_dd.set_valign(Gtk.Align.CENTER)

        category_items = [
            ("Any category", ""),
            ("Optimization", "optimization"),
            ("Utility", "utility"),
            ("Technology", "technology"),
            ("Adventure", "adventure"),
            ("Decoration", "decoration"),
            ("Magic", "magic"),
            ("Storage", "storage"),
            ("Worldgen", "worldgen"),
            ("Library", "library"),
        ]
        cat_dd = Gtk.DropDown.new_from_strings([x[0] for x in category_items])
        cat_dd.set_valign(Gtk.Align.CENTER)

        sort_items = [
            ("Relevance", "relevance"),
            ("Downloads", "downloads"),
            ("Follows", "follows"),
            ("Newest", "newest"),
            ("Recently updated", "updated"),
        ]
        sort_dd = Gtk.DropDown.new_from_strings([x[0] for x in sort_items])
        sort_dd.set_valign(Gtk.Align.CENTER)
        sort_dd.set_selected(1)

        results = Gtk.ListBox()
        results.set_selection_mode(Gtk.SelectionMode.NONE)
        results.add_css_class("mod-results-list")
        results.set_vexpand(True)

        prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        prev_btn.add_css_class("flat")
        next_btn = Gtk.Button(icon_name="go-next-symbolic")
        next_btn.add_css_class("flat")
        page_label = Gtk.Label(label="Page 1/1", xalign=0.0)
        page_label.add_css_class("dim-label")
        results_label = Gtk.Label(label="", xalign=1.0)
        results_label.add_css_class("dim-label")
        results_label.set_ellipsize(Pango.EllipsizeMode.END)
        results_label.set_max_width_chars(16)
        controls_row.append(prev_btn)
        controls_row.append(next_btn)
        controls_row.append(page_label)
        controls_row.append(results_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls_row.append(spacer)

        controls_row.append(type_dd)
        controls_row.append(cat_dd)
        controls_row.append(sort_dd)
        outer.append(controls_row)

        page_size = 10
        state = {"offset": 0, "total": 0, "busy": False}

        def selected_category() -> str:
            idx = int(cat_dd.get_selected())
            if idx < 0 or idx >= len(category_items):
                return ""
            return category_items[idx][1]

        def selected_project_type() -> str:
            idx = int(type_dd.get_selected())
            if idx < 0 or idx >= len(project_type_items):
                return "mod"
            return project_type_items[idx][1]

        def selected_sort() -> str:
            idx = int(sort_dd.get_selected())
            if idx < 0 or idx >= len(sort_items):
                return "downloads"
            return sort_items[idx][1]

        def update_pager():
            total = max(0, int(state["total"]))
            page = (state["offset"] // page_size) + 1
            max_page = max(1, (total + page_size - 1) // page_size)
            page_label.set_label(f"Page {page}/{max_page}")
            prev_btn.set_sensitive((not state["busy"]) and state["offset"] > 0)
            next_btn.set_sensitive(
                (not state["busy"]) and (state["offset"] + page_size < total)
            )

        def set_busy(busy: bool):
            state["busy"] = busy
            entry.set_sensitive(not busy)
            btn.set_sensitive(not busy)
            type_dd.set_sensitive(not busy)
            cat_dd.set_sensitive(not busy)
            sort_dd.set_sensitive(not busy)
            update_pager()

        def update_search_hint() -> None:
            if selected_project_type() == "modpack":
                entry.set_placeholder_text("Search Fabric modpacks…")
            else:
                entry.set_placeholder_text("Search Fabric mods…")

        def clear_results():
            while True:
                r = results.get_row_at_index(0)
                if r is None:
                    break
                results.remove(r)

        def installed_mod_names() -> set[str]:
            root = self._server_dir()
            if not root:
                return set()
            mods_dir = root / "mods"
            if not mods_dir.is_dir():
                return set()
            return {p.name.lower() for p in mods_dir.glob("*.jar")}

        def finish_search(hits, total, err, version, qtxt):
            set_busy(False)
            if err:
                results_label.set_label("Search failed")
                results.append(self._empty_listbox_row("Could not fetch Modrinth results."))
                return
            state["total"] = int(total)
            update_pager()
            results_label.set_label(f"{state['total']:,} results")
            if not hits:
                results.append(self._empty_listbox_row("No results"))
                return

            installed = installed_mod_names()
            for h in hits:
                results.append(self._make_modrinth_row(h, version, installed))

        def do_search(reset: bool = False):
            if reset:
                state["offset"] = 0
            clear_results()
            q = entry.get_text().strip()
            mc_version = self._server_info.mc_version if self._server_info else ""
            qtxt = q
            results_label.set_label("Searching…")
            set_busy(True)
            offset = int(state["offset"])
            category = selected_category() or None
            sort_key = selected_sort()
            project_type = selected_project_type()

            def thread_fn():
                try:
                    hits, total = modrinth_client.search_mods(
                        qtxt,
                        limit=page_size,
                        offset=offset,
                        sort=sort_key,
                        game_version=(mc_version if mc_version else None),
                        category=category,
                        loader="fabric",
                        server_side_only=True,
                        project_type=project_type,
                    )
                    GLib.idle_add(
                        lambda h=hits, t=total, v=mc_version, qq=qtxt: finish_search(
                            h, t, None, v, qq
                        )
                    )
                except Exception as ex:
                    GLib.idle_add(
                        lambda e=str(ex), v=mc_version, qq=qtxt: finish_search(
                            [], 0, e, v, qq
                        )
                    )

            threading.Thread(target=thread_fn, daemon=True).start()

        def on_prev(*_):
            if state["offset"] >= page_size:
                state["offset"] -= page_size
                do_search(reset=False)

        def on_next(*_):
            if state["offset"] + page_size < state["total"]:
                state["offset"] += page_size
                do_search(reset=False)

        # Explicitly propagate events after triggering search to avoid
        # accidentally consuming default focus handling on text widgets.
        def trigger_search(*_):
            update_search_hint()
            do_search(reset=True)
            return False

        btn.connect("clicked", trigger_search)
        entry.connect("activate", trigger_search)
        prev_btn.connect("clicked", on_prev)
        next_btn.connect("clicked", on_next)
        type_dd.connect("notify::selected", trigger_search)
        cat_dd.connect("notify::selected", trigger_search)
        sort_dd.connect("notify::selected", trigger_search)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(results)
        outer.append(sw)

        # Run initial discovery search when opening the page.
        update_search_hint()
        update_pager()
        GLib.idle_add(lambda: do_search(reset=True) or False)
        tv.set_content(outer)
        return tv

    def _empty_listbox_row(self, title: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        label = Gtk.Label(label=title, xalign=0.0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_lines(3)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_top(10)
        label.set_margin_bottom(10)
        row.set_child(label)
        return row

    def _looks_installed(self, hit, installed_names: set[str]) -> bool:
        slug = (hit.slug or "").strip().lower()
        if slug and any(slug in n for n in installed_names):
            return True
        needle = (hit.title or "").strip().lower().replace(" ", "-")
        if needle and any(needle in n for n in installed_names):
            return True
        return False

    def _load_icon_async(self, image: Gtk.Image, url: str) -> None:
        def worker():
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Hosty/1.0 (+https://github.com/hosty)"},
                )
                with urllib.request.urlopen(req, timeout=20.0) as resp:
                    data = resp.read()
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if not pixbuf:
                    return
                scaled = pixbuf.scale_simple(44, 44, GdkPixbuf.InterpType.BILINEAR) or pixbuf
                texture = Gdk.Texture.new_for_pixbuf(scaled)

                def ui_set():
                    image.set_from_paintable(texture)

                GLib.idle_add(ui_set)
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _make_modrinth_row(self, hit, mc_version: str, installed_names: set[str]) -> Gtk.ListBoxRow:
        from hosty.backend import modrinth_client

        is_modpack = str(getattr(hit, "project_type", "mod")).lower() == "modpack"

        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.add_css_class("mod-card-row")
        row.add_css_class("card")
        row.set_margin_bottom(10)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)

        icon = Gtk.Image.new_from_icon_name("application-x-addon-symbolic")
        icon.set_pixel_size(44)
        icon.set_valign(Gtk.Align.START)
        outer.append(icon)
        if hit.icon_url:
            self._load_icon_async(icon, hit.icon_url)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_hexpand(True)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title_author = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_author.set_hexpand(True)

        title = Gtk.Label(label=hit.title, xalign=0.0)
        title.add_css_class("title-4")
        title.set_wrap(False)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        title_author.append(title)

        author_label = Gtk.Label(label=f"by {hit.author or 'Unknown'}", xalign=0.0)
        author_label.add_css_class("caption")
        author_label.add_css_class("dim-label")
        title_author.append(author_label)
        top_row.append(title_author)

        downloads_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        downloads_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        downloads_icon.set_pixel_size(12)
        downloads_box.append(downloads_icon)
        downloads_label = Gtk.Label(
            label=_format_compact_count(int(hit.downloads or 0)),
            xalign=1.0,
        )
        downloads_label.add_css_class("caption")
        downloads_label.add_css_class("dim-label")
        downloads_box.append(downloads_label)
        top_row.append(downloads_box)
        content.append(top_row)

        desc_text = (hit.description or "No description available.").strip()
        desc = Gtk.Label(label=desc_text, xalign=0.0)
        desc.set_wrap(True)
        desc.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        desc.set_lines(2)
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        desc.add_css_class("dim-label")
        content.append(desc)

        version_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        version_dd = Gtk.DropDown.new_from_strings(["Checking versions…"])
        version_dd.set_hexpand(True)
        version_row.append(version_dd)

        install_btn = Gtk.Button(label=("Install pack" if is_modpack else "Install"))
        install_btn.add_css_class("suggested-action")
        install_btn.set_halign(Gtk.Align.START)
        if is_modpack and self._is_modpack_installed(hit.project_id):
            install_btn.set_label("Installed")
            install_btn.set_sensitive(False)
        elif (not is_modpack) and self._looks_installed(hit, installed_names):
            install_btn.set_label("Installed")
            install_btn.set_sensitive(False)
        version_row.append(install_btn)

        open_btn = Gtk.Button(label="Open page")
        open_btn.add_css_class("flat")
        version_row.append(open_btn)
        content.append(version_row)

        outer.append(content)
        row.set_child(outer)

        version_objs = []

        def on_open_page(*_):
            slug = hit.slug or hit.project_id
            route = "modpack" if is_modpack else "mod"
            if not _open_uri(f"https://modrinth.com/{route}/{slug}"):
                self._alert("Could not open browser", "Unable to open the Modrinth page.")

        def selected_version():
            if not version_objs:
                return None
            idx = int(version_dd.get_selected())
            if idx < 0 or idx >= len(version_objs):
                return None
            return version_objs[idx]

        def on_install(*_b):
            if self._is_running():
                self._alert("Server is running", "Stop the server before installing mods.")
                return
            if not mc_version:
                self._alert("Unknown version", "Could not read Minecraft version for this server.")
                return

            chosen = selected_version()
            if not chosen:
                self._alert("No compatible version", "No compatible server version is available.")
                return

            op_token = self._begin_mod_operation()
            if not op_token:
                self._alert("No server selected", "Select a server before installing mods.")
                return

            install_btn.set_label("Installing…")
            install_btn.set_sensitive(False)

            if is_modpack:
                install_btn.set_label("Installing...")

                def ui_ok_pack(downloaded_count: int, override_count: int, managed_mods: list[str]):
                    install_btn.set_label("Installed")
                    install_btn.set_sensitive(False)
                    self._record_modpack_install(
                        hit.project_id,
                        chosen.version_id,
                        version_number=chosen.version_number,
                        title=hit.title,
                        mod_files=sorted(
                            {
                                str(name).strip().lower()
                                for name in managed_mods
                                if str(name).strip().lower().endswith(".jar")
                            }
                        ),
                    )
                    self._toast(
                        f"Installed modpack ({downloaded_count} files)"
                    )
                    self._end_mod_operation(op_token)
                    self._rebuild_lists()

                def ui_err_pack(msg: str):
                    if self._is_modpack_installed(hit.project_id):
                        install_btn.set_label("Installed")
                        install_btn.set_sensitive(False)
                        self._end_mod_operation(op_token)
                        self._alert("Install failed", msg)
                        return
                    install_btn.set_label("Install pack")
                    install_btn.set_sensitive(True)
                    self._end_mod_operation(op_token)
                    self._alert("Install failed", msg)

                def ui_progress_pack(done: int, total: int):
                    if int(total) <= 0:
                        install_btn.set_label("Installing...")
                    else:
                        install_btn.set_label(f"{done}/{total}")

                def install_pack_thread():
                    try:
                        root = self._server_dir()
                        if not root:
                            raise RuntimeError("No server selected.")

                        def on_pack_progress(d: int, t: int, rel_path: str):
                            GLib.idle_add(lambda dd=d, tt=t: ui_progress_pack(dd, tt))

                        result = modrinth_client.install_modpack(
                            chosen.version_id,
                            root,
                            progress_callback=on_pack_progress,
                        )
                        GLib.idle_add(
                            lambda d=result.downloaded_files, o=result.extracted_override_files, m=result.managed_mod_files: ui_ok_pack(d, o, m)
                        )
                    except Exception as e:
                        GLib.idle_add(lambda m=str(e): ui_err_pack(m))

                threading.Thread(target=install_pack_thread, daemon=True).start()
                return

            def ui_ok(fname: str, dep_count: int):
                install_btn.set_label("Installed")
                install_btn.set_sensitive(False)
                self._record_individual_mod_install(
                    hit.project_id,
                    hit.title,
                    chosen.version_id,
                    chosen.filename,
                )
                if dep_count > 0:
                    self._toast(f"Installed {dep_count} required dependencies")
                self._toast(f"Installed {fname}")
                if self._is_running():
                    self._toast("Restart the server for mod changes to apply")
                self._end_mod_operation(op_token)
                self._rebuild_lists()

            def ui_err(msg: str):
                install_btn.set_label("Install")
                install_btn.set_sensitive(True)
                self._end_mod_operation(op_token)
                self._alert("Install failed", msg)

            def thread_fn(deps_to_install: list, all_required_deps: list):
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")

                    mods_dir = root / "mods"
                    mods_dir.mkdir(parents=True, exist_ok=True)
                    installed_names_local = {p.name.lower() for p in mods_dir.glob("*.jar")}

                    installed_dep_count = 0
                    for dep in deps_to_install:
                        dep_name = dep.filename.lower()
                        if dep_name in installed_names_local:
                            continue
                        if dep_name == chosen.filename.lower():
                            continue
                        dep_dest = mods_dir / dep.filename
                        modrinth_client.download_to(dep.download_url, dep_dest)
                        installed_names_local.add(dep_name)
                        installed_dep_count += 1

                    dest = mods_dir / chosen.filename
                    modrinth_client.download_to(chosen.download_url, dest)
                    self._record_dependency_installs(chosen.filename, all_required_deps)
                    GLib.idle_add(lambda f=chosen.filename, c=installed_dep_count: ui_ok(f, c))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err(m))

            def prompt_dependencies(deps_to_install: list, all_required_deps: list):
                if not deps_to_install:
                    threading.Thread(
                        target=thread_fn,
                        args=([], all_required_deps),
                        daemon=True,
                    ).start()
                    return

                dep_names = [d.filename for d in deps_to_install]
                preview = "\n".join([f"- {n}" for n in dep_names[:6]])
                more = ""
                if len(dep_names) > 6:
                    more = f"\n- and {len(dep_names) - 6} more"

                dialog = Adw.AlertDialog()
                dialog.set_heading("Install required dependencies?")
                dialog.set_body(
                    "This mod requires additional dependencies:\n\n"
                    f"{preview}{more}\n\n"
                    "Install them as well?"
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("install", "Install")
                dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("install")
                dialog.set_close_response("cancel")

                def on_response(_d, response):
                    if response == "install":
                        threading.Thread(
                            target=thread_fn,
                            args=(deps_to_install, all_required_deps),
                            daemon=True,
                        ).start()
                    else:
                        install_btn.set_label("Install")
                        install_btn.set_sensitive(True)
                        self._end_mod_operation(op_token)

                dialog.connect("response", on_response)
                dialog.present(self.get_root())

            def resolve_and_prompt():
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")

                    mods_dir = root / "mods"
                    mods_dir.mkdir(parents=True, exist_ok=True)
                    installed_names_local = {p.name.lower() for p in mods_dir.glob("*.jar")}
                    deps = modrinth_client.resolve_required_dependencies(
                        chosen.version_id,
                        mc_version,
                        loader="fabric",
                    )
                    deps_to_install = []
                    for dep in deps:
                        dep_name = dep.filename.lower()
                        if dep_name in installed_names_local:
                            continue
                        if dep_name == chosen.filename.lower():
                            continue
                        deps_to_install.append(dep)

                    GLib.idle_add(lambda d=deps_to_install, a=deps: prompt_dependencies(d, a))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err(m))

            threading.Thread(target=resolve_and_prompt, daemon=True).start()

        def load_versions():
            if not mc_version:
                GLib.idle_add(lambda: version_dd.set_model(Gtk.StringList.new(["No server version"])))
                return
            try:
                versions = modrinth_client.find_compatible_versions(
                    hit.project_id,
                    mc_version,
                    loader="fabric",
                    limit=5,
                )
                if not versions:
                    GLib.idle_add(
                        lambda: version_dd.set_model(Gtk.StringList.new(["No compatible versions"]))
                    )
                    return

                names = []
                seen = set()
                chosen_for_labels = []
                for v in versions:
                    vn = (v.version_number or v.name or "").strip()
                    if not vn or vn in seen:
                        continue
                    seen.add(vn)
                    names.append(vn)
                    chosen_for_labels.append(v)

                if not names:
                    GLib.idle_add(
                        lambda: version_dd.set_model(Gtk.StringList.new(["No compatible versions"]))
                    )
                    return

                version_objs.clear()
                version_objs.extend(chosen_for_labels)

                def ui_set_versions():
                    version_dd.set_model(Gtk.StringList.new(names))
                    version_dd.set_selected(0)

                GLib.idle_add(ui_set_versions)

                first = version_objs[0]
                if (not is_modpack) and first.filename.lower() in installed_names:
                    dependents = self._dependency_dependents(first.filename)
                    if dependents:
                        GLib.idle_add(lambda: install_btn.set_label("Dependency"))
                    else:
                        GLib.idle_add(lambda: install_btn.set_label("Installed"))
                    GLib.idle_add(lambda: install_btn.set_sensitive(False))
            except Exception as e:
                GLib.idle_add(
                    lambda m=str(e): version_dd.set_model(Gtk.StringList.new(["Version lookup failed"]))
                )
                GLib.idle_add(lambda m=str(e): version_dd.set_tooltip_text(m))

        open_btn.connect("clicked", on_open_page)
        install_btn.connect("clicked", on_install)
        threading.Thread(target=load_versions, daemon=True).start()
        return row

    def _on_open_server_folder(self, *_):
        root = self._server_dir()
        if root:
            self._open_target(root)

    def _on_open_mods_folder(self, *_):
        root = self._server_dir()
        if root:
            d = root / "mods"
            d.mkdir(parents=True, exist_ok=True)
            self._open_target(d)

    def _on_open_backups_folder(self, *_):
        bdir = self._backups_dir()
        if not bdir:
            self._alert("No server selected", "Select a server to open backups.")
            return
        self._open_target(bdir)

    def _open_target(self, path: Path):
        if not _open_path(path):
            self._alert("Could not open path", str(path))

    def _trash_dir(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        d = root / ".hosty-trash"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _soft_delete_with_undo(
        self,
        target: Path,
        label: str,
        on_refresh,
        on_finalize=None,
        toast_seconds: int = 6,
    ):
        trash_dir = self._trash_dir()
        if not trash_dir:
            self._alert("No server selected", "Select a server first.")
            return

        trash_name = f"{target.name}.{uuid.uuid4().hex}.trash"
        trashed = trash_dir / trash_name

        try:
            shutil.move(str(target), str(trashed))
        except OSError as e:
            self._alert("Could not delete", str(e))
            return

        state = {"undone": False}

        def undo_delete():
            if state["undone"]:
                return
            state["undone"] = True
            try:
                restore_target = target
                restore_target.parent.mkdir(parents=True, exist_ok=True)
                if restore_target.exists():
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    restore_target = restore_target.with_name(f"{restore_target.stem}-restored-{stamp}{restore_target.suffix}")
                shutil.move(str(trashed), str(restore_target))
                on_refresh()
                self._toast(f"Restored {label}")
            except OSError as e:
                self._alert("Could not undo", str(e))

        def finalize_delete():
            if state["undone"]:
                return False
            try:
                if trashed.is_dir():
                    shutil.rmtree(trashed, ignore_errors=True)
                else:
                    trashed.unlink(missing_ok=True)
            except Exception:
                pass

            if on_finalize:
                try:
                    on_finalize()
                except Exception:
                    pass

            return False

        on_refresh()
        self._toast(f"Deleted {label}", button_label="Undo", on_button=undo_delete, timeout=toast_seconds)
        GLib.timeout_add_seconds(toast_seconds, finalize_delete)

    def _confirm_delete_world(self, path: Path, name: str):
        if self._is_running():
            self._alert("Server is running", "Stop the server before deleting a world.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete world?")
        dialog.set_body(f"Permanently delete “{name}” and all of its contents?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._soft_delete_with_undo(
                    path,
                    f"world \"{name}\"",
                    on_refresh=self._rebuild_lists,
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _confirm_delete_mod(self, path: Path, name: str):
        if self._is_running():
            self._alert("Server is running", "Stop the server before removing mods.")
            return

        dependents = self._dependency_dependents(name)

        def do_delete():
            self._soft_delete_with_undo(
                path,
                f"mod \"{name}\"",
                on_refresh=self._rebuild_lists,
                on_finalize=lambda: self._remove_mod_from_mod_states(name),
            )

        if not dependents:
            do_delete()
            return

        dialog = Adw.AlertDialog()
        if dependents:
            preview = "\n".join([f"- {m}" for m in dependents[:6]])
            more = ""
            if len(dependents) > 6:
                more = f"\n- and {len(dependents) - 6} more"
            dialog.set_heading("Delete dependency mod?")
            dialog.set_body(
                f"The following mods depend on \"{name}\":\n\n"
                f"{preview}{more}\n\n"
                "Are you sure you want to proceed?"
            )
        else:
            dialog.set_heading("Delete mod?")
            dialog.set_body(f"Remove “{name}”?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                do_delete()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _alert(self, title: str, body: str):
        d = Adw.AlertDialog()
        d.set_heading(title)
        d.set_body(body)
        d.add_response("ok", "OK")
        d.present(self.get_root())

    def _toast(
        self,
        message: str,
        button_label: str | None = None,
        on_button=None,
        timeout: int = 3,
    ):
        root = self.get_root()
        if root and hasattr(root, "show_toast"):
            root.show_toast(
                message,
                button_label=button_label,
                on_button=on_button,
                timeout=timeout,
            )
