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
                        version_number = str(raw.get("version_number", "")).strip() or str(raw.get("name", "")).strip()
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
        mods_dir = root / "mods"
        installed = {p.name.lower() for p in mods_dir.glob("*.jar")} if mods_dir.is_dir() else set()
        return [p for p in parents if p in installed]

    def _cleanup_orphaned_dependencies(self, removed_mod_filename: str) -> None:
        """Remove dependency files that are no longer needed after a mod is deleted."""
        removed_key = str(removed_mod_filename).strip().lower()
        if not removed_key:
            return

        root = self._server_dir()
        if not root:
            return

        mods_dir = root / "mods"
        if not mods_dir.is_dir():
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

    def _set_mod_update_row_subtitle(self, subtitle: str) -> None:
        if self._check_updates_row:
            self._check_updates_row.set_subtitle(subtitle)

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
        self._set_mod_update_row_subtitle(_("Checking for updates..."))

        def worker():
            from hosty.shared.backend import modrinth_client

            mc_version = self._server_info.mc_version if self._server_info else ""
            modpack_entries = self._modpack_entries()
            managed_mods = set(self._modpack_managed_mod_map().keys())
            individual_state = self._read_individual_mod_state().get("mods", {})
            datapack_state = self._read_datapack_state().get("datapacks", {})

            refresh_needed = False
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

            # Check standalone updates
            standalone_updates = []
            blocked = 0
            for project_id, meta in individual_state.items():
                current_version = str((meta or {}).get("version_id", "")).strip()
                # Find compatible mod version with fabric loader
                latest = modrinth_client.find_compatible_version(
                    project_id,
                    mc_version,
                    loader="fabric",
                )
                if not latest:
                    continue

                # Ensure the compatible version is actually a mod (has loaders)
                if not latest.loaders or len(latest.loaders) == 0:
                    continue

                # Update metadata if missing (backfilling)
                if not (meta or {}).get("version_number") or not (meta or {}).get("title"):
                    title_to_record = (meta or {}).get("title")
                    if not title_to_record:
                        p_data = modrinth_client.get_project(project_id)
                        if p_data:
                            title_to_record = p_data.get("title")

                    self._record_individual_mod_install(
                        project_id,
                        title_to_record or project_id,
                        current_version,
                        (meta or {}).get("filename"),
                        version_number=(meta or {}).get("version_number") or latest.version_number
                        if latest.version_id == current_version
                        else (meta or {}).get("version_number"),
                    )
                    refresh_needed = True

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

            # Check datapack updates (datapacks have no loader requirement)
            datapack_updates = []
            for project_id, meta in datapack_state.items():
                current_version = str((meta or {}).get("version_id", "")).strip()
                versions = modrinth_client.get_project_versions(project_id)
                # Filter to only datapack versions (no loaders)
                datapack_versions = [v for v in versions if not v.loaders or len(v.loaders) == 0]
                compatible = [v for v in datapack_versions if not mc_version or mc_version in (v.game_versions or [])]
                if not compatible:
                    # Fall back to any datapack version without MC version requirement
                    compatible = datapack_versions
                if not compatible:
                    continue
                latest = compatible[0]

                # Update metadata if missing (backfilling)
                if not (meta or {}).get("version_number") or not (meta or {}).get("title"):
                    title_to_record = (meta or {}).get("title")
                    if not title_to_record:
                        p_data = modrinth_client.get_project(project_id)
                        if p_data:
                            title_to_record = p_data.get("title")

                    self._record_datapack_install(
                        project_id,
                        title_to_record or project_id,
                        current_version,
                        (meta or {}).get("filename"),
                        version_number=(meta or {}).get("version_number") or latest.version_number
                        if latest.version_id == current_version
                        else (meta or {}).get("version_number"),
                    )
                    refresh_needed = True

                if str(latest.version_id).strip() == current_version:
                    continue
                datapack_updates.append((project_id, meta, latest))

            if refresh_needed:
                GLib.idle_add(self._rebuild_lists)

            def show_result():
                total_updates = len(modpack_updates) + len(standalone_updates) + len(datapack_updates)
                if total_updates == 0:
                    self._mods_update_busy = False
                    self._set_mod_update_row_subtitle(_("Update check complete"))
                    if blocked > 0:
                        self._toast(
                            _("No safe updates found ({} blocked by modpack-managed dependencies)").format(blocked)
                        )
                    else:
                        self._toast(_("All tracked mods and datapacks are up to date"))
                    return False

                lines: list[str] = []
                if modpack_updates:
                    lines.append(_("Modpacks:"))
                    for pid, entry, newer in modpack_updates[:12]:
                        title = str(entry.get("title", "")).strip() or pid
                        vn = str(newer.version_number or newer.version_id)
                        lines.append(_("- {} -> {}").format(title, vn))
                    if len(modpack_updates) > 12:
                        lines.append(_("- and {} more modpacks").format(len(modpack_updates) - 12))

                if standalone_updates:
                    if lines:
                        lines.append("")
                    lines.append(_("Standalone mods:"))
                    for pid, meta, newer, _deps in standalone_updates[:14]:
                        title = str((meta or {}).get("title", "")).strip() or pid
                        vn = str(newer.version_number or newer.version_id)
                        lines.append(_("- {} -> {}").format(title, vn))
                    if len(standalone_updates) > 14:
                        lines.append(_("- and {} more mods").format(len(standalone_updates) - 14))

                if datapack_updates:
                    if lines:
                        lines.append("")
                    lines.append(_("Datapacks:"))
                    for pid, meta, newer in datapack_updates[:14]:
                        title = str((meta or {}).get("title", "")).strip() or pid
                        vn = str(newer.version_number or newer.version_id)
                        lines.append(_("- {} -> {}").format(title, vn))
                    if len(datapack_updates) > 14:
                        lines.append(_("- and {} more datapacks").format(len(datapack_updates) - 14))

                listing = "\n".join(lines)

                body_parts = []
                if modpack_updates or standalone_updates:
                    body_parts.append(
                        _("Found {} modpack update(s) and {} standalone mod update(s).").format(
                            len(modpack_updates), len(standalone_updates)
                        )
                    )
                if datapack_updates:
                    body_parts.append(_("Found {} datapack update(s).").format(len(datapack_updates)))
                if blocked > 0:
                    body_parts.append(
                        _("{} standalone update(s) were skipped because dependencies are managed by a modpack.").format(
                            blocked
                        )
                    )
                if listing:
                    body_parts.append(listing)

                dialog = Adw.AlertDialog()
                dialog.set_heading(_("Install available updates?"))
                dialog.set_body("\n\n".join(body_parts))
                dialog.add_response("cancel", _("Cancel"))
                dialog.add_response("update", _("Update"))
                dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("update")
                dialog.set_close_response("cancel")

                def on_response(_d, response):
                    if response != "update":
                        self._mods_update_busy = False
                        self._set_mod_update_row_subtitle(_("Update check complete"))
                        return

                    op_token = self._begin_mod_operation()
                    if not op_token:
                        self._mods_update_busy = False
                        self._set_mod_update_row_subtitle(_("Update check complete"))
                        self._alert(_("No server selected"), _("Select a server before updating mods."))
                        return

                    self._set_mod_update_row_subtitle(_("Updating mods..."))
                    self._toast(
                        _("Updating {} modpack(s), {} mod(s), and {} datapack(s)").format(
                            len(modpack_updates), len(standalone_updates), len(datapack_updates)
                        )
                    )
                    threading.Thread(
                        target=self._apply_mod_updates,
                        args=(modpack_updates, standalone_updates, op_token, datapack_updates),
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
        mod_operation_token: str | None = None,
        datapack_updates: list | None = None,
    ) -> None:
        from hosty.shared.backend import modrinth_client

        root = self._server_dir()
        if not root:
            GLib.idle_add(lambda: self._alert(_("No server selected"), _("Select a server to update mods.")))
            GLib.idle_add(lambda: self._set_mod_update_row_subtitle(_("Update check complete")))
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
                    _("Updating modpack {}/{}: {}").format(i, total, t)
                )
            )
            try:
                previous_mods = {
                    str(m).strip().lower() for m in (entry.get("mods") or []) if str(m).strip().lower().endswith(".jar")
                }

                def on_progress(done: int, total: int, rel_path: str):
                    GLib.idle_add(
                        lambda d=done, t=total: self._set_mod_update_row_subtitle(
                            _("Updating {}: {}/{}").format(pack_title, d, t)
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
                    _("Updating standalone mod {}/{}: {}").format(i, total, t)
                )
            )
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
                    modrinth_client.download_to(dep.download_url, mods_dir / dep.filename)

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

                modrinth_client.download_to(latest.download_url, mods_dir / latest.filename)
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

        # Apply datapack updates.
        dp_updates = datapack_updates or []
        dp_dir = self._datapacks_dir()
        if dp_dir:
            dp_dir.mkdir(parents=True, exist_ok=True)
        for index, (project_id, meta, latest) in enumerate(dp_updates, start=1):
            dp_title = str((meta or {}).get("title", "")).strip() or project_id
            GLib.idle_add(
                lambda i=index, total=len(dp_updates), t=dp_title: self._set_mod_update_row_subtitle(
                    _("Updating datapack {}/{}: {}").format(i, total, t)
                )
            )
            try:
                if not dp_dir:
                    raise RuntimeError("No datapacks folder available.")
                old_filename = str((meta or {}).get("filename", "")).strip()
                dest = dp_dir / latest.filename
                modrinth_client.download_to(latest.download_url, dest)
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

        def finish_ui():
            self._mods_update_busy = False
            self._set_mod_update_row_subtitle(_("Update check complete"))
            self._end_mod_operation(mod_operation_token)
            self._rebuild_lists()
            if failed == 0:
                self._toast(_("Applied {} update(s)").format(applied))
            else:
                self._toast(_("Applied {} update(s), {} failed").format(applied, failed))
            return False

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
            dialog.set_body(_("Remove \u201c{}\u201d?").format(name))
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
