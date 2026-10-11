"""
FilesView -- folders, worlds, backups, and Modrinth integration (per selected server).
"""

from __future__ import annotations

import ast
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, GLib, Gtk

from hosty.gtk_ui.dialogs.dialog_common import (
    apply_dialog_size,
    build_progress_page,
    make_dialog_header,
)

from ..utils import *


class ModsMixin:
    def _begin_mod_operation(self) -> str | None:
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

    def _end_mod_operation(self, token: str | None) -> None:
        if not token:
            return
        server_id = None
        with self._mod_operation_lock:
            server_id = self._active_mod_operation_tokens.pop(token, None)
        if server_id and self._server_manager:
            self._server_manager.end_mod_operation(server_id)

    def _mod_dependency_state_path(self) -> Path | None:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-mod-dependencies.json"

    def _modpack_state_path(self) -> Path | None:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-modpacks.json"

    def _individual_mod_state_path(self) -> Path | None:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-mod-installs.json"

    def _datapack_state_path(self) -> Path | None:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-datapack-installs.json"

    def _datapacks_dir(self) -> Path | None:
        """Return the active datapacks directory (world/datapacks), creating parent if needed."""
        root = self._server_dir()
        if not root:
            return None
        # Try to find the active world folder from server.properties
        world_name = "world"
        props = root / "server.properties"
        if props.exists():
            try:
                for line in props.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("level-name="):
                        world_name = stripped[len("level-name=") :].strip() or "world"
                        break
            except Exception:
                pass
        return root / world_name / "datapacks"

    def _read_datapack_state(self) -> dict:
        path = self._datapack_state_path()
        if not path or not path.exists():
            return {"datapacks": {}}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return {"datapacks": {}}
            dp_raw = raw.get("datapacks") if isinstance(raw.get("datapacks"), dict) else {}
            cleaned: dict[str, dict[str, str]] = {}
            for project_id, item in dp_raw.items():
                pid = str(project_id).strip()
                if not pid or not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                version_id = str(item.get("version_id", "")).strip()
                version_number = str(item.get("version_number", "")).strip()
                filename = str(item.get("filename", "")).strip()
                if not filename:
                    continue
                cleaned[pid] = {
                    "title": title,
                    "version_id": version_id,
                    "version_number": version_number,
                    "filename": filename,
                }
            return {"datapacks": cleaned}
        except Exception:
            return {"datapacks": {}}

    def _write_datapack_state(self, state: dict) -> bool:
        path = self._datapack_state_path()
        if not path:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception:
            return False

    def _record_datapack_install(
        self,
        project_id: str,
        title: str,
        version_id: str,
        filename: str,
        version_number: str = "",
    ) -> None:
        pid = str(project_id).strip()
        if not pid:
            return
        state = self._read_datapack_state()
        dps = state.setdefault("datapacks", {})
        dps[pid] = {
            "title": str(title or "").strip(),
            "version_id": str(version_id or "").strip(),
            "version_number": str(version_number or "").strip(),
            "filename": str(filename or "").strip(),
        }
        self._write_datapack_state(state)

    def _is_datapack_installed(self, project_id: str) -> bool:
        pid = str(project_id).strip()
        if not pid:
            return False
        state = self._read_datapack_state()
        return pid in state.get("datapacks", {})

    def _is_mod_present(self, project_id: str, installed_names) -> bool:
        """Whether any recognized build of a tracked mod is on disk.

        True when the recorded file is present locally (any version — the
        caller already ruled out an exact current-file match).
        """
        pid = str(project_id).strip()
        if not pid:
            return False
        names = {str(n).lower() for n in (installed_names or set())}
        state = self._read_individual_mod_state()
        record = (state.get("mods", {}) or {}).get(pid) or {}
        recorded_file = str(record.get("filename", "")).strip().lower()
        return bool(recorded_file) and recorded_file in names

    def _make_datapack_row(self, project_id: str, meta: dict) -> Adw.ActionRow:
        title = str(meta.get("title", "")).strip() or project_id
        filename = str(meta.get("filename", "")).strip()
        version_id = str(meta.get("version_id", "")).strip()
        version_number = str(meta.get("version_number", "")).strip()

        row = Adw.ActionRow(title=title)
        subtitle_bits = []
        if filename:
            dp_dir = self._datapacks_dir()
            if dp_dir:
                jar = dp_dir / filename
                if jar.exists():
                    subtitle_bits.append(_format_size(jar.stat().st_size))
        if version_number:
            subtitle_bits.append(_("version {}").format(version_number))
        elif version_id:
            subtitle_bits.append(_("version {}").format(version_id[:8]))
        row.set_subtitle(" · ".join(subtitle_bits) if subtitle_bits else "")
        row.set_activatable(False)

        open_btn = self._icon_button(
            "web-browser-symbolic",
            _("Open datapack page"),
            lambda *_p, pid=project_id: _open_uri(f"https://modrinth.com/datapack/{pid}"),
        )
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            _("Delete datapack"),
            lambda *_p, pid=project_id, t=title, fn=filename: self._confirm_delete_datapack(pid, t, fn),
            destructive=True,
        )
        row.add_suffix(open_btn)
        row.add_suffix(delete_btn)
        return row

    def _confirm_delete_datapack(self, project_id: str, title: str, filename: str) -> None:
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before deleting a datapack."))
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete datapack?"))
        dialog.set_body(_('Remove "{}" from this server?').format(title))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._delete_datapack(project_id, title, filename)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _delete_datapack(self, project_id: str, title: str, filename: str) -> None:
        dp_dir = self._datapacks_dir()
        removed = False
        if dp_dir and filename:
            target = dp_dir / filename
            if target.exists():
                target.unlink(missing_ok=True)
                removed = True

        state = self._read_datapack_state()
        dps = state.get("datapacks", {})
        if isinstance(dps, dict):
            dps.pop(project_id, None)
            self._write_datapack_state({"datapacks": dps})

        self._rebuild_lists()
        suffix = _(" (file deleted)") if removed else ""
        self._toast(_("Deleted {}{}").format(title, suffix))

    def _read_modpack_state(self) -> dict:
        path = self._modpack_state_path()
        if not path or not path.exists():
            return {"installed_projects": {}}

        try:
            with open(path, encoding="utf-8") as f:
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
            with open(path, encoding="utf-8") as f:
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
                version_number = str(item.get("version_number", "")).strip()
                filename = str(item.get("filename", "")).strip()
                if not filename:
                    continue

                cleaned[pid] = {
                    "title": title,
                    "version_id": version_id,
                    "version_number": version_number,
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
        version_number: str = "",
    ) -> None:
        pid = str(project_id).strip()
        if not pid:
            return

        state = self._read_individual_mod_state()
        mods = state.setdefault("mods", {})
        mods[pid] = {
            "title": str(title or "").strip(),
            "version_id": str(version_id or "").strip(),
            "version_number": str(version_number or "").strip(),
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
        mod_files: list[str] | None = None,
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

    def _find_mod_jar_path(self, mods_dir: Path, filename: str) -> Path | None:
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
            if not str(entry.get("version_number", "")).strip() and str(entry.get("version_id", "")).strip()
        ]
        if not missing:
            return

        self._modpack_version_enrich_busy = True

        def worker():
            from concurrent.futures import ThreadPoolExecutor

            from hosty.shared.backend import modrinth_client

            latest_entries = self._modpack_entries()
            changed = False
            payload: dict[str, dict[str, Any]] = {}

            def fetch_number(version_id: str) -> str:
                try:
                    raw = modrinth_client.get_version(version_id)
                except Exception:
                    return ""
                if isinstance(raw, dict):
                    return str(raw.get("version_number", "")).strip() or str(raw.get("name", "")).strip()
                return ""

            missing_numbers = {
                project_id: str(entry.get("version_id", "")).strip()
                for project_id, entry in latest_entries.items()
                if not str(entry.get("version_number", "")).strip() and str(entry.get("version_id", "")).strip()
            }
            fetched: dict[str, str] = {}
            if missing_numbers:
                with ThreadPoolExecutor(max_workers=6, thread_name_prefix="hosty-enrich") as pool:
                    for project_id, number in zip(missing_numbers, pool.map(fetch_number, missing_numbers.values())):
                        fetched[project_id] = number

            for project_id, entry in latest_entries.items():
                version_id = str(entry.get("version_id", "")).strip()
                version_number = str(entry.get("version_number", "")).strip()
                if not version_number and version_id:
                    version_number = fetched.get(project_id, "")
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
            with open(path, encoding="utf-8") as f:
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
                        parent_keys = sorted({str(p).strip().lower() for p in parents if str(p).strip()})
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

            dep_project_id = str(getattr(dep, "project_id", "")).strip()
            dep_title = str(getattr(dep, "title", "") or getattr(dep, "name", "") or dep_project_id or dep_key).strip()
            dep_version_id = str(getattr(dep, "version_id", "")).strip()
            dep_version_number = str(getattr(dep, "version_number", "")).strip()
            dep_filename = str(getattr(dep, "filename", "")).strip()
            if dep_project_id and dep_filename:
                self._record_individual_mod_install(
                    dep_project_id,
                    dep_title or dep_project_id,
                    dep_version_id,
                    dep_filename,
                    version_number=dep_version_number,
                )

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
        mods_dir = self._content_dir(root)
        installed = {p.name.lower() for p in mods_dir.glob("*.jar")} if mods_dir and mods_dir.is_dir() else set()
        return [p for p in parents if p in installed]

    def _cleanup_orphaned_dependencies(self, removed_mod_filename: str) -> None:
        """Remove dependency files that are no longer needed after a mod is deleted."""
        removed_key = str(removed_mod_filename).strip().lower()
        if not removed_key:
            return

        root = self._server_dir()
        if not root:
            return

        mods_dir = self._content_dir(root)
        if not mods_dir or not mods_dir.is_dir():
            return

        state = self._read_mod_dependency_state()
        req = state.get("required_by", {})

        # Collect all dependencies that need to be checked
        deps_to_check = []

        # Find dependencies that this mod required
        for dep_key, parents in req.items():
            if removed_key not in parents:
                continue
            deps_to_check.append(dep_key)

        # Now check each dependency to see if it's still needed by other mods
        for dep_key in deps_to_check:
            parents = req.get(dep_key, [])
            # Remove the old parent from the parents list
            remaining_parents = [p for p in parents if p != removed_key]

            if not remaining_parents:
                # This dependency is now orphaned, try to remove it
                try:
                    dep_path = self._find_mod_jar_path(mods_dir, dep_key)
                    if dep_path and dep_path.exists():
                        dep_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _make_mod_row(self, jar: Path) -> Adw.ActionRow:
        # Try to find metadata for this jar
        filename_lower = jar.name.lower()
        mod_state = self._read_individual_mod_state().get("mods", {})

        project_id = None
        version_id = None
        version_number = None
        mod_title = None
        for pid, meta in mod_state.items():
            if str(meta.get("filename", "")).lower() == filename_lower:
                project_id = pid
                version_id = meta.get("version_id")
                version_number = meta.get("version_number")
                mod_title = meta.get("title")
                break

        row = Adw.ActionRow(title=mod_title or jar.name)
        subtitle_bits = [_format_size(jar.stat().st_size)]

        dependents = self._dependency_dependents(jar.name)
        if dependents:
            subtitle_bits.append(_("Dependency"))

        if version_number:
            subtitle_bits.append(_("version {}").format(version_number))
        elif version_id:
            subtitle_bits.append(_("version {}").format(version_id[:8]))

        row.set_subtitle(" · ".join(subtitle_bits))
        row.set_activatable(False)

        if project_id:
            open_btn = self._icon_button(
                "web-browser-symbolic",
                _("Open mod page"),
                lambda *_p, pid=project_id: _open_uri(f"https://modrinth.com/mod/{pid}"),
            )
            row.add_suffix(open_btn)

        del_btn = self._icon_button(
            "user-trash-symbolic",
            _("Delete mod"),
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
        subtitle_bits = [_("{} managed mods").format(len(mods))]
        if version_number:
            subtitle_bits.append(_("version {}").format(version_number))
        elif version_id:
            subtitle_bits.append(_("version {}").format(version_id[:8]))
        row.set_subtitle(" · ".join(subtitle_bits))
        row.set_activatable(False)

        view_btn = self._icon_button(
            "view-list-symbolic",
            _("View managed mods"),
            lambda *_p, t=title, m=mods: self._show_modpack_mods_dialog(t, m),
        )
        open_btn = self._icon_button(
            "web-browser-symbolic",
            _("Open modpack page"),
            lambda *_p, pid=project_id: _open_uri(f"https://modrinth.com/modpack/{pid}"),
        )
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            _("Delete modpack"),
            lambda *_p, pid=project_id, t=title: self._confirm_delete_modpack(pid, t),
            destructive=True,
        )
        row.add_suffix(view_btn)
        row.add_suffix(open_btn)
        row.add_suffix(delete_btn)
        return row

    def _confirm_delete_modpack(self, project_id: str, title: str) -> None:
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before deleting a modpack."))
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete modpack?"))
        dialog.set_body(_('Remove "{}" and delete its managed mod files from this server?').format(title))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
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
            self._alert(_("No server selected"), _("Select a server before deleting a modpack."))
            return

        mods_dir = self._content_dir(root)
        if not mods_dir:
            return
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
        self._toast(_("Deleted {} ({} mod files removed)").format(title, removed_count))

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
            d.set_body(_("No tracked mod files for this modpack yet."))
            d.add_response("ok", _("OK"))
            d.present(self.get_root())
            return

        cleaned = sorted(set(cleaned), key=str.lower)
        d.set_body(_("{} managed mods").format(len(cleaned)))

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

        d.add_response("ok", _("OK"))
        d.present(self.get_root())

    def _on_check_mod_updates(self, *_args) -> None:
        if self._mods_update_busy:
            self._toast(_("Mod update check already running"))
            return
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before checking for mod updates."))
            return
        if not self._server_info or not self._server_info.mc_version:
            self._alert(_("Unknown version"), _("Could not determine Minecraft version for this server."))
            return

        self._mods_update_busy = True

        state: dict = {
            "open": True,
            "token": None,
            "blocked": 0,
            "modpacks": [],
            "mods": [],
            "datapacks": [],
            "plugins": [],
            "selected": set(),
            "checks": [],
            "select_all_row": None,
            "syncing_select_all": False,
        }

        dialog = Adw.Dialog()
        apply_dialog_size(dialog, _("Updates"))

        toolbar = Adw.ToolbarView()
        header, cancel_btn, primary_btn = make_dialog_header(
            cancel_label=_("Cancel"),
            primary_label=_("Install"),
        )
        primary_btn.set_visible(False)
        toolbar.add_top_bar(header)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        checking_page = Adw.StatusPage()
        checking_page.set_icon_name("software-update-available-symbolic")
        checking_page.set_title(_("Checking for updates..."))
        checking_spinner = Gtk.Spinner()
        checking_spinner.start()
        checking_page.set_child(checking_spinner)
        stack.add_named(checking_page, "checking")

        review_page = Adw.PreferencesPage()
        review_page.set_vexpand(True)
        bulk_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bulk_box.set_halign(Gtk.Align.CENTER)
        bulk_box.set_margin_top(12)
        bulk_box.set_margin_bottom(6)
        bulk_box.set_visible(False)
        select_all_btn = Gtk.Button(label=_("Select all"))
        select_all_btn.connect(
            "clicked",
            lambda *_: [c.set_active(True) for c in list(state["checks"])],
        )
        unselect_all_btn = Gtk.Button(label=_("Unselect all"))
        unselect_all_btn.connect(
            "clicked",
            lambda *_: [c.set_active(False) for c in list(state["checks"])],
        )
        bulk_box.append(select_all_btn)
        bulk_box.append(unselect_all_btn)
        review_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        review_content.append(bulk_box)
        review_content.append(review_page)
        scrolled_review = Gtk.ScrolledWindow()
        scrolled_review.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_review.set_child(review_content)
        stack.add_named(scrolled_review, "review")

        progress_page, progress_status, progress_bar, progress_label = build_progress_page(
            icon_name="folder-download-symbolic",
            title=_("Updating"),
            description=_("Preparing update"),
        )
        # Legacy name kept for the callbacks below (StatusPage API).
        progress_status_row = progress_status
        stack.add_named(progress_page, "progress")

        done_page = Adw.StatusPage()
        stack.add_named(done_page, "done")

        toolbar.set_content(stack)
        dialog.set_child(toolbar)

        def set_primary(label: str | None, sensitive: bool = True) -> None:
            if label is None:
                primary_btn.set_visible(False)
                return
            primary_btn.set_visible(True)
            primary_btn.set_label(label)
            primary_btn.set_sensitive(sensitive)

        def show_done(icon: str, title: str, description: str) -> None:
            done_page.set_icon_name(icon)
            done_page.set_title(title)
            done_page.set_description(description)
            cancel_btn.set_visible(False)
            set_primary(_("Close"))
            stack.set_visible_child_name("done")

        def start_install() -> None:
            selected: set = set(state.get("selected") or set())

            def _is_selected(kind: str, pid: str) -> bool:
                return f"{kind}:{pid}" in selected

            filtered_modpacks = [x for x in state["modpacks"] if _is_selected("modpack", str(x[0]))]
            filtered_mods = [x for x in state["mods"] if _is_selected("mod", str(x[0]))]
            filtered_datapacks = [x for x in state["datapacks"] if _is_selected("datapack", str(x[0]))]
            filtered_plugins = [x for x in state["plugins"] if _is_selected("plugin", str(x[0]))]
            if not (filtered_modpacks or filtered_mods or filtered_datapacks or filtered_plugins):
                self._toast(_("Select at least one update to install"))
                return
            op_token = self._begin_mod_operation()
            if not op_token:
                self._mods_update_busy = False
                self._alert(_("No server selected"), _("Select a server before updating mods."))
                return
            state["token"] = op_token
            cancel_btn.set_sensitive(False)
            set_primary(None)
            progress_bar.set_fraction(0.0)
            progress_label.set_label("")
            progress_status_row.set_title(_("Updating"))
            progress_status_row.set_description("")
            stack.set_visible_child_name("progress")

            def on_progress(frac: float, message: str) -> None:
                def update():
                    progress_bar.set_fraction(max(0.0, min(1.0, float(frac))))
                    progress_label.set_label(str(message))
                    return False

                GLib.idle_add(update)

            def on_done(applied: int, failed: int) -> None:
                if not state["open"]:
                    return
                if applied == 0 and failed == 0:
                    show_done(
                        "dialog-information-symbolic",
                        _("Nothing was updated"),
                        _("No updates could be applied."),
                    )
                elif failed:
                    # Translators: e.g. "1 update applied", "3 updates applied"
                    applied_str = ngettext("{count} update applied.", "{count} updates applied.", applied).format(
                        count=applied
                    )
                    # Translators: e.g. "1 failed", "3 failed"
                    failed_str = ngettext("{count} failed.", "{count} failed.", failed).format(count=failed)
                    show_done(
                        "dialog-warning-symbolic",
                        _("Updates finished with errors"),
                        f"{applied_str} {failed_str}",
                    )
                else:
                    # Translators: e.g. "1 update applied", "3 updates applied"
                    applied_str = ngettext("{count} update applied.", "{count} updates applied.", applied).format(
                        count=applied
                    )
                    blocked = int(state.get("blocked") or 0)
                    if blocked:
                        # Translators: e.g. "1 update was skipped", "3 updates were skipped"
                        skipped_str = ngettext(
                            "{count} update was skipped because dependencies are managed by a modpack.",
                            "{count} updates were skipped because dependencies are managed by a modpack.",
                            blocked,
                        ).format(count=blocked)
                        message = f"{applied_str} {skipped_str}"
                    else:
                        message = applied_str
                    show_done(
                        "object-select-symbolic",
                        _("All updates installed"),
                        message,
                    )

            # Build a plural-correct list like "1 modpack, 3 mods" instead of "modpack(s)".
            update_parts: list[str] = []
            modpack_count = len(filtered_modpacks)
            if modpack_count:
                # Translators: e.g. "1 modpack", "3 modpacks"
                update_parts.append(
                    ngettext("{count} modpack", "{count} modpacks", modpack_count).format(count=modpack_count)
                )
            mod_count = len(filtered_mods)
            if mod_count:
                # Translators: e.g. "1 mod", "3 mods"
                update_parts.append(ngettext("{count} mod", "{count} mods", mod_count).format(count=mod_count))
            datapack_count = len(filtered_datapacks)
            if datapack_count:
                # Translators: e.g. "1 datapack", "3 datapacks"
                update_parts.append(
                    ngettext("{count} datapack", "{count} datapacks", datapack_count).format(count=datapack_count)
                )
            plugin_count = len(filtered_plugins)
            if plugin_count:
                # Translators: e.g. "1 plugin", "3 plugins"
                update_parts.append(
                    ngettext("{count} plugin", "{count} plugins", plugin_count).format(count=plugin_count)
                )
            if update_parts:
                # Translators: {items} is a comma-separated list like "1 modpack, 3 mods"
                self._toast(_("Updating {items}.").format(items=", ".join(update_parts)))
            else:
                self._toast(_("Updating selected items."))
            threading.Thread(
                target=self._apply_mod_updates,
                args=(
                    filtered_modpacks,
                    filtered_mods,
                    op_token,
                    filtered_datapacks,
                    filtered_plugins,
                    on_progress,
                    on_done,
                ),
                daemon=True,
            ).start()

        def on_cancel(*_args):
            dialog.close()

        def on_primary(*_args):
            if stack.get_visible_child_name() == "review":
                start_install()
            else:
                dialog.close()

        def on_closed(*_args):
            state["open"] = False
            if not state["token"]:
                self._mods_update_busy = False

        cancel_btn.connect("clicked", on_cancel)
        primary_btn.connect("clicked", on_primary)
        dialog.connect("closed", on_closed)
        dialog.present(self.get_root())

        def worker():
            from concurrent.futures import ThreadPoolExecutor, as_completed

            from hosty.shared.backend import modrinth_client

            mc_version = self._server_info.mc_version if self._server_info else ""
            modpack_entries = self._modpack_entries()
            managed_mods = set(self._modpack_managed_mod_map().keys())
            individual_state = self._read_individual_mod_state().get("mods", {})
            datapack_state = self._read_datapack_state().get("datapacks", {})
            plugin_state = self._read_plugin_state().get("plugins", {})
            loader_type = self._server_loader()

            def check_modpack(project_id, entry):
                """Return the newer version, or None (pure network + compute)."""
                current_version = str(entry.get("version_id", "")).strip()
                current_version_number = str(entry.get("version_number", "")).strip()
                versions = modrinth_client.get_project_versions(
                    project_id,
                    game_versions=[mc_version] if mc_version else None,
                )
                compatible = [v for v in versions if mc_version in (v.game_versions or [])]
                if not compatible:
                    return None
                latest = compatible[0]

                latest_id = str(latest.version_id).strip()
                latest_number = str(latest.version_number).strip()
                same_id = current_version and (latest_id == current_version)
                same_number = current_version_number and (latest_number == current_version_number)
                return None if (same_id or same_number) else latest

            def check_mod(project_id, meta):
                """Return (status, newer, deps, backfill).

                status is "update", "blocked", or "none". backfill is a
                callable applying the metadata backfill, or None. Disk writes
                stay out of worker threads; the caller applies backfills
                sequentially.
                """
                current_version = str((meta or {}).get("version_id", "")).strip()
                # Find compatible mod version for the server's loader
                latest = modrinth_client.find_compatible_version(
                    project_id,
                    mc_version,
                    loader=loader_type,
                )
                if not latest:
                    return ("none", None, [], None)

                # Ensure the compatible version is actually a mod (has loaders)
                if not latest.loaders or len(latest.loaders) == 0:
                    return ("none", None, [], None)

                # Update metadata if missing (backfilling)
                backfill = None
                if not (meta or {}).get("version_number") or not (meta or {}).get("title"):
                    title_to_record = (meta or {}).get("title")
                    if not title_to_record:
                        p_data = modrinth_client.get_project(project_id)
                        if p_data:
                            title_to_record = p_data.get("title")

                    def backfill(
                        project_id=project_id,
                        title_to_record=title_to_record,
                        current_version=current_version,
                        meta=meta,
                        latest=latest,
                    ):
                        self._record_individual_mod_install(
                            project_id,
                            title_to_record or project_id,
                            current_version,
                            (meta or {}).get("filename"),
                            version_number=(meta or {}).get("version_number") or latest.version_number
                            if latest.version_id == current_version
                            else (meta or {}).get("version_number"),
                        )

                if str(latest.version_id).strip() == current_version:
                    return ("none", None, [], backfill)

                deps = modrinth_client.resolve_required_dependencies(
                    latest.version_id,
                    mc_version,
                    loader=loader_type,
                )
                dep_hits_modpack = any(str(dep.filename).strip().lower() in managed_mods for dep in deps)
                if dep_hits_modpack:
                    return ("blocked", None, [], backfill)

                return ("update", latest, deps, backfill)

            def check_datapack(project_id, meta):
                """Return (newer, backfill); newer is None when up to date."""
                current_version = str((meta or {}).get("version_id", "")).strip()
                versions = modrinth_client.get_project_versions(
                    project_id,
                    game_versions=[mc_version] if mc_version else None,
                )
                # Datapack versions carry loaders == ["datapack"] (older ones
                # may be untagged).
                datapack_versions = [v for v in versions if modrinth_client.is_datapack_version(v)]
                compatible = [v for v in datapack_versions if not mc_version or mc_version in (v.game_versions or [])]
                if not compatible:
                    # Fall back to any datapack version without MC version requirement
                    compatible = datapack_versions
                if not compatible:
                    return (None, None)
                latest = compatible[0]

                # Update metadata if missing (backfilling)
                backfill = None
                if not (meta or {}).get("version_number") or not (meta or {}).get("title"):
                    title_to_record = (meta or {}).get("title")
                    if not title_to_record:
                        p_data = modrinth_client.get_project(project_id)
                        if p_data:
                            title_to_record = p_data.get("title")

                    def backfill(
                        project_id=project_id,
                        title_to_record=title_to_record,
                        current_version=current_version,
                        meta=meta,
                        latest=latest,
                    ):
                        self._record_datapack_install(
                            project_id,
                            title_to_record or project_id,
                            current_version,
                            (meta or {}).get("filename"),
                            version_number=(meta or {}).get("version_number") or latest.version_number
                            if latest.version_id == current_version
                            else (meta or {}).get("version_number"),
                        )

                if str(latest.version_id).strip() == current_version:
                    return (None, backfill)
                return (latest, backfill)

            def check_plugin(project_id, meta):
                """Return (newer, backfill); newer is None when up to date."""
                current_version = str((meta or {}).get("version_id", "")).strip()
                latest = modrinth_client.find_compatible_plugin_version(project_id, mc_version)
                if not latest:
                    return (None, None)

                backfill = None
                if not (meta or {}).get("version_number") or not (meta or {}).get("title"):
                    title_to_record = (meta or {}).get("title")
                    if not title_to_record:
                        p_data = modrinth_client.get_project(project_id)
                        if p_data:
                            title_to_record = p_data.get("title")

                    def backfill(
                        project_id=project_id,
                        title_to_record=title_to_record,
                        current_version=current_version,
                        meta=meta,
                        latest=latest,
                    ):
                        self._record_plugin_install(
                            project_id,
                            title_to_record or project_id,
                            current_version,
                            (meta or {}).get("filename"),
                            version_number=(meta or {}).get("version_number") or latest.version_number
                            if latest.version_id == current_version
                            else (meta or {}).get("version_number"),
                        )

                if str(latest.version_id).strip() == current_version:
                    return (None, backfill)
                return (latest, backfill)

            def run_task(task):
                kind, project_id, payload = task
                if kind == "modpack":
                    return ("modpack", check_modpack(project_id, payload))
                if kind == "mod":
                    return ("mod",) + check_mod(project_id, payload)
                if kind == "datapack":
                    return ("datapack",) + check_datapack(project_id, payload)
                return ("plugin",) + check_plugin(project_id, payload)

            tasks = (
                [("modpack", pid, entry) for pid, entry in modpack_entries.items()]
                + [("mod", pid, meta) for pid, meta in individual_state.items()]
                + [("datapack", pid, meta) for pid, meta in datapack_state.items()]
                + [("plugin", pid, meta) for pid, meta in plugin_state.items()]
            )
            results: dict[tuple[str, str], tuple] = {}
            with ThreadPoolExecutor(max_workers=8, thread_name_prefix="hosty-update-check") as pool:
                pending = {pool.submit(run_task, task): task for task in tasks}
                for future in as_completed(pending):
                    task = pending[future]
                    try:
                        results[(task[0], str(task[1]))] = future.result()
                    except Exception:
                        # One project's network failure must not fail the
                        # whole check; it simply yields no update.
                        pass

            # Rebuild the update lists in the original order so the review UI
            # stays deterministic, and apply backfills sequentially (disk
            # read-modify-write is not safe to parallelize).
            modpack_updates = []
            standalone_updates = []
            datapack_updates = []
            plugin_updates = []
            blocked = 0
            refresh_needed = False
            for kind, project_id, payload in tasks:
                result = results.get((kind, str(project_id)))
                if not result:
                    continue
                if kind == "modpack":
                    if result[1] is not None:
                        modpack_updates.append((project_id, payload, result[1]))
                elif kind == "mod":
                    _kind, status, newer, deps, backfill = result
                    if backfill is not None:
                        try:
                            backfill()
                            refresh_needed = True
                        except Exception:
                            pass
                    if status == "update":
                        standalone_updates.append((project_id, payload, newer, deps))
                    elif status == "blocked":
                        blocked += 1
                elif kind == "datapack":
                    _kind, newer, backfill = result
                    if backfill is not None:
                        try:
                            backfill()
                            refresh_needed = True
                        except Exception:
                            pass
                    if newer is not None:
                        datapack_updates.append((project_id, payload, newer))
                else:
                    _kind, newer, backfill = result
                    if backfill is not None:
                        try:
                            backfill()
                            refresh_needed = True
                        except Exception:
                            pass
                    if newer is not None:
                        plugin_updates.append((project_id, payload, newer))

            if refresh_needed:
                GLib.idle_add(self._rebuild_lists)

            def show_result():
                if not state["open"]:
                    self._mods_update_busy = False
                    return False
                state["blocked"] = blocked
                state["modpacks"] = modpack_updates
                state["mods"] = standalone_updates
                state["datapacks"] = datapack_updates
                state["plugins"] = plugin_updates
                total_updates = (
                    len(modpack_updates) + len(standalone_updates) + len(datapack_updates) + len(plugin_updates)
                )
                if total_updates == 0:
                    self._mods_update_busy = False
                    if blocked > 0:
                        show_done(
                            "dialog-information-symbolic",
                            _("You're up to date"),
                            _("No safe updates found ({} blocked by modpack-managed dependencies)").format(blocked),
                        )
                    else:
                        show_done(
                            "object-select-symbolic",
                            _("You're up to date"),
                            _("All tracked mods, plugins and datapacks are up to date"),
                        )
                    return False

                def version_line(old: str, new: str) -> str:
                    old_v = str(old or "").strip()
                    new_v = str(new or "").strip()
                    if old_v and new_v:
                        return _("{} → {}").format(old_v, new_v)
                    return new_v or old_v

                for group in state.setdefault("review_groups", []):
                    try:
                        review_page.remove(group)
                    except Exception:
                        pass
                state["review_groups"] = []
                state["selected"] = set()
                state["checks"] = []

                all_keys: list[str] = (
                    [f"modpack:{pid}" for pid, _e, _n in modpack_updates]
                    + [f"mod:{pid}" for pid, _m, _n, _d in standalone_updates]
                    + [f"plugin:{pid}" for pid, _m, _n in plugin_updates]
                    + [f"datapack:{pid}" for pid, _m, _n in datapack_updates]
                )
                state["selected"] = set(all_keys)
                total_count = len(all_keys)

                def refresh_primary() -> None:
                    count = len(state["selected"])
                    if count == 0:
                        set_primary(_("Install"), sensitive=False)
                    elif count == total_count:
                        set_primary(_("Install"))
                    else:
                        set_primary(_("Install ({})").format(count))

                bulk_box.set_visible(True)

                def on_item_toggled(key: str, active: bool) -> None:
                    if active:
                        state["selected"].add(key)
                    else:
                        state["selected"].discard(key)
                    refresh_primary()

                def add_kind_group(title: str, items: list[tuple[str, str, str, str]]) -> None:
                    group = Adw.PreferencesGroup(title=title)
                    for key, item_title, old_v, new_v in items:
                        row = Adw.ActionRow(title=item_title, subtitle=version_line(old_v, new_v))
                        check = Gtk.CheckButton()
                        check.set_active(True)
                        check.set_valign(Gtk.Align.CENTER)
                        check.connect("toggled", lambda c, k=key: on_item_toggled(k, c.get_active()))
                        row.add_prefix(check)
                        row.set_activatable(True)
                        row.connect("activated", lambda r, c=check: c.set_active(not c.get_active()))
                        group.add(row)
                        state["checks"].append(check)
                    review_page.add(group)
                    state["review_groups"].append(group)

                if modpack_updates:
                    add_kind_group(
                        _("Modpacks ({})").format(len(modpack_updates)),
                        [
                            (
                                f"modpack:{pid}",
                                str(entry.get("title", "")).strip() or pid,
                                str(entry.get("version_number", "")).strip(),
                                str(newer.version_number or newer.version_id),
                            )
                            for pid, entry, newer in modpack_updates
                        ],
                    )
                if standalone_updates:
                    add_kind_group(
                        _("Mods ({})").format(len(standalone_updates)),
                        [
                            (
                                f"mod:{pid}",
                                str((meta or {}).get("title", "")).strip() or pid,
                                str((meta or {}).get("version_number", "")).strip(),
                                str(newer.version_number or newer.version_id),
                            )
                            for pid, meta, newer, _deps in standalone_updates
                        ],
                    )
                if plugin_updates:
                    add_kind_group(
                        _("Plugins ({})").format(len(plugin_updates)),
                        [
                            (
                                f"plugin:{pid}",
                                str((meta or {}).get("title", "")).strip() or pid,
                                str((meta or {}).get("version_number", "")).strip(),
                                str(newer.version_number or newer.version_id),
                            )
                            for pid, meta, newer in plugin_updates
                        ],
                    )
                if datapack_updates:
                    add_kind_group(
                        _("Datapacks ({})").format(len(datapack_updates)),
                        [
                            (
                                f"datapack:{pid}",
                                str((meta or {}).get("title", "")).strip() or pid,
                                str((meta or {}).get("version_number", "")).strip(),
                                str(newer.version_number or newer.version_id),
                            )
                            for pid, meta, newer in datapack_updates
                        ],
                    )
                if blocked:
                    notes = Adw.PreferencesGroup(title=_("Notes"))
                    blocked_count = int(blocked)
                    # Translators: e.g. "1 standalone update was skipped", "3 standalone updates were skipped"
                    note = Adw.ActionRow(
                        title=_("Skipped updates"),
                        subtitle=ngettext(
                            "{count} standalone update was skipped because dependencies are managed by a modpack.",
                            "{count} standalone updates were skipped because dependencies are managed by a modpack.",
                            blocked_count,
                        ).format(count=blocked_count),
                    )
                    note.set_activatable(False)
                    notes.add(note)
                    review_page.add(notes)
                    state["review_groups"].append(notes)

                cancel_btn.set_visible(True)
                cancel_btn.set_sensitive(True)
                refresh_primary()
                stack.set_visible_child_name("review")
                return False

            GLib.idle_add(show_result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_mod_updates(
        self,
        modpack_updates: list,
        standalone_updates: list,
        mod_operation_token: str | None = None,
        datapack_updates: list | None = None,
        plugin_updates: list | None = None,
        progress_callback=None,
        done_callback=None,
    ) -> None:
        """Apply mod/modpack/datapack/plugin updates on a worker thread.

        ``progress_callback(fraction, message)`` is invoked from the worker
        thread after each item (wrap widget access in ``GLib.idle_add``).
        ``done_callback(applied, failed)`` runs on the main thread via
        ``finish_ui``; when given, the summary toast is skipped so the caller
        (e.g. the update dialog) can present the result itself.
        """
        from hosty.shared.backend import modrinth_client

        def _finish_early() -> None:
            GLib.idle_add(lambda: self._alert(_("No server selected"), _("Select a server to update mods.")))
            GLib.idle_add(lambda: setattr(self, "_mods_update_busy", False))
            GLib.idle_add(lambda t=mod_operation_token: self._end_mod_operation(t))
            if done_callback is not None:
                GLib.idle_add(lambda: done_callback(0, 0))

        root = self._server_dir()
        if not root:
            _finish_early()
            return

        mods_dir = self._content_dir(root)
        if not mods_dir:
            _finish_early()
            return
        mods_dir.mkdir(parents=True, exist_ok=True)

        applied = 0
        failed = 0
        total_items = (
            len(modpack_updates or [])
            + len(standalone_updates or [])
            + len(datapack_updates or [])
            + len(plugin_updates or [])
        )
        done_items = 0

        def _report(label: str) -> None:
            """Forward per-item progress (called from the worker thread)."""
            nonlocal done_items
            if progress_callback is None or total_items <= 0:
                return
            done_items += 1
            try:
                progress_callback(min(1.0, done_items / total_items), label)
            except Exception:
                pass

        # Apply modpack updates first so pack-managed versions remain authoritative.
        for index, (project_id, entry, newer_version) in enumerate(modpack_updates, start=1):
            pack_title = str(entry.get("title", "")).strip() or project_id
            try:
                previous_mods = {
                    str(m).strip().lower() for m in (entry.get("mods") or []) if str(m).strip().lower().endswith(".jar")
                }

                result = modrinth_client.install_modpack(newer_version.version_id, root)

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
            _report(_("Updating modpack {}/{}: {}").format(index, len(modpack_updates), pack_title))

        managed_mods = set(self._modpack_managed_mod_map().keys())

        # Apply standalone updates, installing required dependencies first.
        for index, (project_id, meta, latest, deps) in enumerate(standalone_updates, start=1):
            mod_title = str((meta or {}).get("title", "")).strip() or project_id
            try:
                old_name = str((meta or {}).get("filename", "")).strip()
                deps_to_install = [dep for dep in deps if str(dep.filename).strip().lower() not in managed_mods]

                # Get old dependencies from state before updating
                old_dep_names = set()
                dep_state = self._read_mod_dependency_state()
                for dep_key, parents in dep_state.get("required_by", {}).items():
                    if old_name.lower() in [p.lower() for p in parents]:
                        old_dep_names.add(dep_key)

                # Download new dependencies
                new_dep_names = {str(dep.filename).strip().lower() for dep in deps_to_install}
                for dep in deps_to_install:
                    modrinth_client.download_to(dep.download_url, mods_dir / dep.filename, expected_hashes=dep.hashes)

                # Remove old dependencies that are no longer needed
                removed_deps = old_dep_names - new_dep_names
                for removed_dep in removed_deps:
                    try:
                        dep_path = self._find_mod_jar_path(mods_dir, removed_dep)
                        if dep_path and dep_path.exists():
                            # Check if any other mod needs this dependency
                            remaining_parents = [
                                p
                                for p in dep_state.get("required_by", {}).get(removed_dep, [])
                                if p.lower() != old_name.lower()
                            ]
                            if not remaining_parents:
                                dep_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                modrinth_client.download_to(
                    latest.download_url, mods_dir / latest.filename, expected_hashes=latest.hashes
                )
                if old_name and old_name.lower() != latest.filename.lower():
                    old_path = self._find_mod_jar_path(mods_dir, old_name)
                    if old_path and old_path.exists():
                        old_path.unlink(missing_ok=True)
                    self._remove_mod_from_mod_states(old_name)
                    # Clean up old dependency relationships and orphaned dependency mods
                    self._cleanup_orphaned_dependencies(old_name)
                    self._remove_mod_from_dependency_state(old_name)

                self._record_individual_mod_install(
                    project_id,
                    mod_title,
                    latest.version_id,
                    latest.filename,
                    version_number=latest.version_number,
                )
                self._record_dependency_installs(latest.filename, deps_to_install)
                applied += 1
            except Exception:
                failed += 1
            _report(_("Updating standalone mod {}/{}: {}").format(index, len(standalone_updates), mod_title))

        # Apply datapack updates.
        dp_updates = datapack_updates or []
        dp_dir = self._datapacks_dir()
        if dp_dir:
            dp_dir.mkdir(parents=True, exist_ok=True)
        for index, (project_id, meta, latest) in enumerate(dp_updates, start=1):
            dp_title = str((meta or {}).get("title", "")).strip() or project_id
            try:
                if not dp_dir:
                    raise RuntimeError("No datapacks folder available.")
                old_filename = str((meta or {}).get("filename", "")).strip()
                dest = dp_dir / latest.filename
                modrinth_client.download_to(latest.download_url, dest, expected_hashes=latest.hashes)
                if old_filename and old_filename.lower() != latest.filename.lower():
                    old_path = dp_dir / old_filename
                    if old_path.exists():
                        old_path.unlink(missing_ok=True)
                self._record_datapack_install(
                    project_id,
                    dp_title,
                    latest.version_id,
                    latest.filename,
                    version_number=latest.version_number,
                )
                applied += 1
            except Exception:
                failed += 1
            _report(_("Updating datapack {}/{}: {}").format(index, len(dp_updates), dp_title))

        # Apply plugin updates.
        for index, (project_id, meta, latest) in enumerate(plugin_updates or [], start=1):
            plugin_title = str((meta or {}).get("title", "")).strip() or project_id
            try:
                plugins_dir = self._plugins_dir()
                if not plugins_dir:
                    raise RuntimeError("This server does not support plugins.")
                plugins_dir.mkdir(parents=True, exist_ok=True)
                old_filename = str((meta or {}).get("filename", "")).strip()
                dest = plugins_dir / latest.filename
                modrinth_client.download_to(latest.download_url, dest, expected_hashes=latest.hashes)
                if old_filename and old_filename.lower() != latest.filename.lower():
                    old_path = plugins_dir / old_filename
                    if old_path.exists():
                        old_path.unlink(missing_ok=True)
                self._record_plugin_install(
                    project_id,
                    plugin_title,
                    latest.version_id,
                    latest.filename,
                    version_number=latest.version_number,
                )
                applied += 1
            except Exception:
                failed += 1
            _report(_("Updating plugin {}/{}: {}").format(index, len(plugin_updates or []), plugin_title))

        def finish_ui():
            self._mods_update_busy = False
            self._end_mod_operation(mod_operation_token)
            self._rebuild_lists()
            if done_callback is not None:
                try:
                    done_callback(applied, failed)
                except Exception:
                    pass
            elif failed == 0:
                # Translators: e.g. "1 update applied", "3 updates applied"
                msg = ngettext("{count} update applied.", "{count} updates applied.", applied).format(count=applied)
                self._toast(msg)
            else:
                # Translators: e.g. "1 update applied", "3 updates applied"
                applied_str = ngettext("{count} update applied.", "{count} updates applied.", applied).format(
                    count=applied
                )
                # Translators: e.g. "1 failed", "3 failed"
                failed_str = ngettext("{count} failed.", "{count} failed.", failed).format(count=failed)
                self._toast(f"{applied_str} {failed_str}")
            return False

        # Pick back up version-parked mods that now have a compatible
        # release (e.g. tunnel requirements parked at install time).
        try:
            if self._server_manager and self._server_info:
                restored, restore_failed = self._server_manager.retry_incompatible_components(self._server_info.id)
                applied += restored
                failed += restore_failed
        except Exception:
            pass

        GLib.idle_add(finish_ui)

    def _confirm_delete_mod(self, path: Path, name: str):
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before removing mods."))
            return

        dependents = self._dependency_dependents(name)

        def do_delete():
            self._soft_delete_with_undo(
                path,
                f'mod "{name}"',
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
            dialog.set_heading(_("Delete dependency mod?"))
            dialog.set_body(
                _('The following mods depend on "{}":\n\n{}{}\n\n{}').format(
                    name, preview, more, _("Are you sure you want to proceed?")
                )
            )
        else:
            dialog.set_heading(_("Delete mod?"))
            dialog.set_body(_("Remove “{}”?").format(name))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                do_delete()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    # ----- Bukkit plugins (Arclight servers, plugins/ directory) -----

    def _plugin_state_path(self) -> Path | None:
        root = self._server_dir()
        if not root:
            return None
        return root / ".hosty-plugin-installs.json"

    def _read_plugin_state(self) -> dict:
        path = self._plugin_state_path()
        if not path or not path.exists():
            return {"plugins": {}}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return {"plugins": {}}
            plugins_raw = raw.get("plugins") if isinstance(raw.get("plugins"), dict) else {}
            cleaned: dict[str, dict[str, str]] = {}
            for project_id, item in plugins_raw.items():
                pid = str(project_id).strip()
                if not pid or not isinstance(item, dict):
                    continue
                filename = str(item.get("filename", "")).strip()
                if not filename:
                    continue
                cleaned[pid] = {
                    "title": str(item.get("title", "")).strip(),
                    "version_id": str(item.get("version_id", "")).strip(),
                    "version_number": str(item.get("version_number", "")).strip(),
                    "filename": filename,
                }
            return {"plugins": cleaned}
        except Exception:
            return {"plugins": {}}

    def _write_plugin_state(self, state: dict) -> bool:
        path = self._plugin_state_path()
        if not path:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception:
            return False

    def _record_plugin_install(
        self,
        project_id: str,
        title: str,
        version_id: str,
        filename: str,
        version_number: str = "",
    ) -> None:
        pid = str(project_id).strip()
        if not pid:
            return
        state = self._read_plugin_state()
        plugins = state.setdefault("plugins", {})
        plugins[pid] = {
            "title": str(title or "").strip(),
            "version_id": str(version_id or "").strip(),
            "version_number": str(version_number or "").strip(),
            "filename": str(filename or "").strip(),
        }
        self._write_plugin_state(state)

    def _is_plugin_installed(self, project_id: str) -> bool:
        pid = str(project_id).strip()
        if not pid:
            return False
        return pid in self._read_plugin_state().get("plugins", {})

    def _remove_plugin_from_state(self, removed_filename: str) -> None:
        key = str(removed_filename).strip().lower()
        if not key:
            return
        state = self._read_plugin_state()
        plugins = dict(state.get("plugins", {}))
        kept = {
            pid: meta for pid, meta in plugins.items() if str((meta or {}).get("filename", "")).strip().lower() != key
        }
        if kept != plugins:
            self._write_plugin_state({"plugins": kept})

    def _plugins_dir(self) -> Path | None:
        """Return the server's plugins/ directory (Arclight hybrid servers)."""
        from hosty.shared.utils.constants import supports_plugins

        root = self._server_dir()
        if not root:
            return None
        if self._server_info is not None and not supports_plugins(getattr(self._server_info, "loader_type", None)):
            return None
        return root / "plugins"

    def _make_plugin_row(self, jar: Path) -> Adw.ActionRow:
        filename_lower = jar.name.lower()
        plugin_state = self._read_plugin_state().get("plugins", {})

        project_id = None
        version_id = None
        version_number = None
        plugin_title = None
        for pid, meta in plugin_state.items():
            if str(meta.get("filename", "")).lower() == filename_lower:
                project_id = pid
                version_id = meta.get("version_id")
                version_number = meta.get("version_number")
                plugin_title = meta.get("title")
                break

        row = Adw.ActionRow(title=plugin_title or jar.name)
        subtitle_bits = [_format_size(jar.stat().st_size)]
        if version_number:
            subtitle_bits.append(_("version {}").format(version_number))
        elif version_id:
            subtitle_bits.append(_("version {}").format(version_id[:8]))
        row.set_subtitle(" · ".join(subtitle_bits))
        row.set_activatable(False)

        if project_id:
            open_btn = self._icon_button(
                "web-browser-symbolic",
                _("Open plugin page"),
                lambda *_p, pid=project_id: _open_uri(f"https://modrinth.com/plugin/{pid}"),
            )
            row.add_suffix(open_btn)

        del_btn = self._icon_button(
            "user-trash-symbolic",
            _("Delete plugin"),
            lambda *_p, p=jar, n=jar.name: self._confirm_delete_plugin(p, n),
            destructive=True,
        )
        row.add_suffix(del_btn)
        return row

    def _make_plugin_row_from_meta(self, project_id: str, meta: dict, plugins_dir: Path) -> Adw.ActionRow | None:
        """Row for a tracked plugin; falls back to a missing-file row."""
        filename = str((meta or {}).get("filename", "")).strip()
        if filename:
            direct = plugins_dir / filename
            if direct.is_file():
                return self._make_plugin_row(direct)
            lowered = filename.lower()
            for jar in plugins_dir.glob("*.jar"):
                if jar.name.lower() == lowered:
                    return self._make_plugin_row(jar)
        title = str((meta or {}).get("title", "")).strip() or project_id
        row = Adw.ActionRow(title=title, subtitle=_("File missing"))
        row.set_activatable(False)
        forget_btn = self._icon_button(
            "user-trash-symbolic",
            _("Remove plugin record"),
            lambda *_p, pid=project_id, fn=filename: self._forget_plugin_record(pid, fn),
            destructive=True,
        )
        row.add_suffix(forget_btn)
        return row

    def _forget_plugin_record(self, project_id: str, filename: str) -> None:
        state = self._read_plugin_state()
        plugins = dict(state.get("plugins", {}))
        plugins.pop(str(project_id).strip(), None)
        self._write_plugin_state({"plugins": plugins})
        self._rebuild_lists()
        self._toast(_("Removed {}").format(str(filename).strip() or project_id))

    def _confirm_delete_plugin(self, path: Path, name: str):
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before removing plugins."))
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete plugin?"))
        dialog.set_body(_("Remove “{}”?").format(name))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._soft_delete_with_undo(
                    path,
                    _('plugin "{}"').format(name),
                    on_refresh=self._rebuild_lists,
                    on_finalize=lambda: self._remove_plugin_from_state(name),
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())
