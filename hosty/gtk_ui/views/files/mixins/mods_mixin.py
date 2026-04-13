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

class ModsMixin:
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
            from hosty.shared.backend import modrinth_client

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
            from hosty.shared.backend import modrinth_client

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
        from hosty.shared.backend import modrinth_client

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

