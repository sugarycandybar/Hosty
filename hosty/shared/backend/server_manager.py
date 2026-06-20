"""
ServerManager - CRUD operations for server instances.
Handles persistence, creation workflow, and server lifecycle.
"""

import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from hosty.shared.backend.config_manager import ConfigManager
from hosty.shared.backend.download_manager import DownloadManager
from hosty.shared.backend.java_manager import JavaManager
from hosty.shared.backend.playit_config import load_playit_config
from hosty.shared.backend.playit_manager import PlayitManager
from hosty.shared.backend.preferences_manager import PreferencesManager
from hosty.shared.backend.server_process import ServerProcess
from hosty.shared.core.events import EventEmitter
from hosty.shared.utils.constants import (
    CONFIG_FILE,
    DEFAULT_RAM_MB,
    SERVERS_DIR,
    get_required_java_version,
)


class ServerInfo:
    """Data class for server metadata."""

    def __init__(self, data: dict):
        self.id: str = data.get("id", str(uuid.uuid4()))
        self.name: str = data.get("name", _("Unnamed Server"))
        self.mc_version: str = data.get("mc_version", "")
        self.loader_version: str = data.get("loader_version", "")
        self.ram_mb: int = data.get("ram_mb", DEFAULT_RAM_MB)
        self.java_version: int = data.get("java_version", 21)
        self.icon_path: str = data.get("icon_path", "")
        self.created_at: str = data.get("created_at", datetime.now().isoformat())
        self.path: str = data.get("path", "")
        self.autostart: bool = data.get("autostart", False)

    @property
    def server_dir(self) -> Path:
        """Get the server directory path."""
        if self.path:
            return Path(self.path)
        return SERVERS_DIR / self.id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mc_version": self.mc_version,
            "loader_version": self.loader_version,
            "ram_mb": self.ram_mb,
            "java_version": self.java_version,
            "icon_path": self.icon_path,
            "created_at": self.created_at,
            "path": str(self.server_dir),
            "autostart": self.autostart,
        }


class ServerManager(EventEmitter):
    """
    Manages all server instances: CRUD, persistence, and process management.
    """

    def __init__(self):
        super().__init__()
        self._servers: dict[str, ServerInfo] = {}
        self._processes: dict[str, ServerProcess] = {}
        self._mods_operation_counts: dict[str, int] = {}
        self.java_manager = JavaManager()
        self.download_manager = DownloadManager()
        self.playit_manager = PlayitManager()
        self.preferences = PreferencesManager()
        self._load()

    def _load(self):
        """Load servers from persisted JSON."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    data = json.load(f)
                for entry in data.get("servers", []):
                    info = ServerInfo(entry)
                    self._servers[info.id] = info
            except Exception as e:
                print(f"Failed to load servers: {e}")

    def _save(self):
        """Persist servers to JSON."""
        data = {"servers": [s.to_dict() for s in self._servers.values()]}
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Failed to save servers: {e}")

    @property
    def servers(self) -> list[ServerInfo]:
        """Get all servers sorted by creation date."""
        return sorted(self._servers.values(), key=lambda s: s.created_at)

    def get_server(self, server_id: str) -> ServerInfo | None:
        """Get a server by ID."""
        return self._servers.get(server_id)

    def add_server(
        self, name: str, mc_version: str, loader_version: str = "", ram_mb: int = DEFAULT_RAM_MB
    ) -> ServerInfo:
        """
        Create and register a new server.
        Does NOT install Fabric -- call install_server() separately.
        """
        server_id = str(uuid.uuid4())
        java_ver = get_required_java_version(mc_version)

        info = ServerInfo(
            {
                "id": server_id,
                "name": name,
                "mc_version": mc_version,
                "loader_version": loader_version,
                "ram_mb": ram_mb,
                "java_version": java_ver,
                "path": str(SERVERS_DIR / server_id),
            }
        )

        # Create server directory
        info.server_dir.mkdir(parents=True, exist_ok=True)

        self._servers[server_id] = info
        self._save()
        self.emit_on_main_thread("server-added", server_id)

        return info

    def rename_server(self, server_id: str, new_name: str):
        """Rename a server."""
        info = self._servers.get(server_id)
        if info:
            info.name = new_name
            self._save()
            self.emit_on_main_thread("server-changed", server_id)

    def set_server_icon(self, server_id: str, icon_path: str):
        """Set the icon for a server."""
        info = self._servers.get(server_id)
        if info:
            info.icon_path = icon_path
            self._save()
            self.emit_on_main_thread("server-changed", server_id)

    def get_autostart_server(self) -> ServerInfo | None:
        """Get the first server configured to auto-start, if any."""
        for server in self._servers.values():
            if server.autostart:
                return server
        return None

    def get_autostart_servers(self) -> list[ServerInfo]:
        """Get all servers configured to auto-start."""
        return [server for server in self._servers.values() if server.autostart]

    def set_server_autostart(self, server_id: str, autostart: bool) -> tuple[bool, str | None]:
        """Enable or disable autostart for a server. Returns (success, error_msg)."""
        info = self._servers.get(server_id)
        if not info:
            return False, _("Server not found.")

        info.autostart = autostart
        self._save()
        self.emit_on_main_thread("server-changed", server_id)
        return True, None

    def update_server_ram(self, server_id: str, ram_mb: int):
        """Update RAM allocation for a server."""
        info = self._servers.get(server_id)
        if info:
            info.ram_mb = ram_mb
            self._save()
            proc = self._processes.get(server_id)
            if proc:
                proc.ram_mb = ram_mb
            self.emit_on_main_thread("server-changed", server_id)

    def update_server_version(self, server_id: str, mc_version: str) -> tuple[bool, str]:
        """Update the Minecraft and Fabric version for a server."""
        return self.update_server_runtime(server_id, mc_version, None)

    def update_server_runtime(
        self,
        server_id: str,
        mc_version: str,
        loader_version: str | None = None,
        progress_callback=None,
        compatibility_plan: dict | None = None,
    ) -> tuple[bool, str]:
        """Install a new Minecraft/Fabric runtime, update compatible content, and isolate incompatible content."""
        from hosty.shared.utils.constants import get_required_java_version

        info = self._servers.get(server_id)
        if not info:
            return False, _("Server not found")

        process = self._processes.get(server_id)
        if process and process.is_running:
            return False, _("Cannot update version while server is running")

        mc_version = str(mc_version or "").strip()
        loader_version = str(loader_version or "").strip() if loader_version is not None else info.loader_version
        if not mc_version:
            return False, _("Minecraft version is required")

        try:
            java_req = get_required_java_version(mc_version)
        except Exception:
            java_req = 21  # Default fallback

        root = info.server_dir
        root.mkdir(parents=True, exist_ok=True)

        def progress(frac: float, msg: str) -> None:
            if progress_callback:
                progress_callback(frac, msg)

        progress(0.02, _("Creating full backup"))
        backup_ok, backup_msg = self.create_full_backup(server_id)
        if not backup_ok:
            return False, _("Could not create full backup before updating: {}").format(backup_msg)

        if not self.java_manager.is_java_available(java_req):
            ok, msg = self.java_manager.download_jre_sync(
                java_req,
                progress_callback=lambda f, text: progress(0.05 + f * 0.20, text),
            )
            if not ok:
                return False, _("Failed to download Java {}: {}").format(java_req, msg)

        progress(0.28, _("Downloading Fabric installer"))
        installer_path = self.download_manager.download_installer(
            progress_callback=lambda f, text: progress(0.28 + f * 0.12, text),
        )
        if not installer_path:
            return False, _("Failed to download Fabric installer")

        for filename in ("server.jar", "fabric-server-launch.jar"):
            try:
                (root / filename).unlink(missing_ok=True)
            except Exception:
                pass

        progress(0.42, _("Downloading Minecraft {} server").format(mc_version))
        ok, msg = self.download_manager.download_server_jar(
            mc_version,
            str(root),
            progress_callback=lambda f, text: progress(0.42 + f * 0.22, text),
        )
        if not ok:
            return False, msg

        java_path = self.java_manager.get_java_path(java_req) or self.java_manager.get_java_for_mc(mc_version) or "java"
        progress(0.66, _("Installing Fabric server"))
        ok, msg = self.download_manager.install_fabric_server(
            java_path=java_path,
            installer_jar=installer_path,
            mc_version=mc_version,
            server_dir=str(root),
            loader_version=loader_version or None,
            progress_callback=lambda f, text: progress(0.66 + f * 0.30, text),
        )
        if not ok:
            return False, msg

        progress(0.90, _("Checking installed content compatibility"))
        plan = compatibility_plan or self.scan_update_compatibility(server_id, mc_version)

        progress(0.93, _("Updating compatible mods and datapacks"))
        applied, failed = self.apply_compatible_component_updates(server_id, mc_version, plan)

        progress(0.97, _("Moving incompatible files aside"))
        disabled = self.isolate_incompatible_components(server_id, mc_version, plan)

        info.mc_version = mc_version
        info.loader_version = loader_version
        info.java_version = java_req
        self._save()
        existing_process = self._processes.get(server_id)
        if existing_process:
            existing_process.java_path = (
                self.java_manager.get_java_path(java_req) or self.java_manager.get_java_for_mc(mc_version) or "java"
            )
        self.emit_on_main_thread("server-changed", server_id)
        progress(1.0, _("Server runtime updated"))

        disabled_count = sum(len(v) for v in disabled.values())
        detail = _("Updated to Minecraft {}.").format(mc_version) + _(" Updated {} compatible file(s).").format(applied)
        if disabled_count:
            detail += _(" Disabled {} incompatible file(s).").format(disabled_count)
        if failed:
            detail += _(" {} compatible update(s) failed.").format(failed)
        return True, detail

    def _json_file(self, path: Path) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_json_file(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _server_datapacks_dir(self, root: Path) -> Path:
        world_name = self._configured_level_name(root)
        return root / world_name / "datapacks"

    def _unique_disabled_path(self, dest_dir: Path, filename: str) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        candidate = dest_dir / Path(filename).name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        idx = 2
        while True:
            alt = dest_dir / f"{stem}-{idx}{suffix}"
            if not alt.exists():
                return alt
            idx += 1

    def _move_if_present(self, source_dir: Path, filename: str, dest_dir: Path) -> Path | None:
        name = Path(str(filename or "")).name
        if not name:
            return None
        source = source_dir / name
        if not source.exists():
            for item in source_dir.glob("*"):
                if item.name.casefold() == name.casefold():
                    source = item
                    break
        if not source.exists() or not source.is_file():
            return None
        dest = self._unique_disabled_path(dest_dir, source.name)
        shutil.move(str(source), str(dest))
        return dest

    @staticmethod
    def version_sort_key(value: str) -> tuple:
        """Natural sort key for Minecraft/Fabric version strings."""
        text = str(value or "").strip().lower()
        parts: list[object] = []
        for token in re.findall(r"\d+|[a-z]+", text):
            if token.isdigit():
                parts.append((0, int(token)))
            else:
                label_weight = {
                    "snapshot": -4,
                    "pre": -3,
                    "rc": -2,
                    "alpha": -5,
                    "beta": -4,
                }.get(token, -1)
                parts.append((1, label_weight, token))
        return tuple(parts)

    @classmethod
    def is_version_at_least(cls, candidate: str, current: str) -> bool:
        return cls.version_sort_key(candidate) >= cls.version_sort_key(current)

    @classmethod
    def is_version_after(cls, candidate: str, current: str) -> bool:
        return cls.version_sort_key(candidate) > cls.version_sort_key(current)

    def _tracked_mod_state(self, root: Path) -> dict:
        data = self._json_file(root / ".hosty-mod-installs.json")
        return data.get("mods") if isinstance(data.get("mods"), dict) else {}

    def _tracked_modpack_state(self, root: Path) -> dict:
        data = self._json_file(root / ".hosty-modpacks.json")
        return data.get("installed_projects") if isinstance(data.get("installed_projects"), dict) else {}

    def _tracked_datapack_state(self, root: Path) -> dict:
        data = self._json_file(root / ".hosty-datapack-installs.json")
        return data.get("datapacks") if isinstance(data.get("datapacks"), dict) else {}

    def _tracked_mod_dependency_state(self, root: Path) -> dict[str, list[str]]:
        data = self._json_file(root / ".hosty-mod-dependencies.json")
        req = data.get("required_by") if isinstance(data.get("required_by"), dict) else {}
        cleaned: dict[str, list[str]] = {}
        for dep_name, parents in req.items():
            dep_key = Path(str(dep_name or "")).name.casefold()
            if not dep_key or not isinstance(parents, list):
                continue
            parent_keys = sorted(
                {Path(str(parent or "")).name.casefold() for parent in parents if Path(str(parent or "")).name}
            )
            if parent_keys:
                cleaned[dep_key] = parent_keys
        return cleaned

    def _write_mod_dependency_state(self, root: Path, required_by: dict[str, list[str]]) -> None:
        cleaned = {
            Path(str(dep)).name.casefold(): sorted(
                {Path(str(parent)).name.casefold() for parent in parents if Path(str(parent)).name}
            )
            for dep, parents in required_by.items()
            if Path(str(dep)).name and parents
        }
        cleaned = {dep: parents for dep, parents in cleaned.items() if parents}
        self._write_json_file(root / ".hosty-mod-dependencies.json", {"required_by": cleaned})

    def _replace_mod_dependency_parent(
        self,
        root: Path,
        old_parent_filename: str,
        new_parent_filename: str,
        dep_versions: list,
    ) -> tuple[set[str], set[str]]:
        old_parent = Path(str(old_parent_filename or "")).name.casefold()
        new_parent = Path(str(new_parent_filename or "")).name.casefold()
        state = self._tracked_mod_dependency_state(root)
        old_dep_names = {dep_name for dep_name, parents in state.items() if old_parent and old_parent in parents}

        for dep_name, parents in list(state.items()):
            filtered = [parent for parent in parents if parent != old_parent]
            if filtered:
                state[dep_name] = filtered
            else:
                state.pop(dep_name, None)

        new_dep_names: set[str] = set()
        if new_parent:
            for dep in dep_versions:
                dep_name = Path(str(getattr(dep, "filename", "") or "")).name.casefold()
                if not dep_name or dep_name == new_parent:
                    continue
                new_dep_names.add(dep_name)
                parents = set(state.get(dep_name, []))
                parents.add(new_parent)
                state[dep_name] = sorted(parents)

        self._write_mod_dependency_state(root, state)
        return old_dep_names, new_dep_names

    def _remove_orphaned_dependency_files(self, root: Path, dependency_names: set[str]) -> None:
        if not dependency_names:
            return
        mods_dir = root / "mods"
        if not mods_dir.is_dir():
            return
        state = self._tracked_mod_dependency_state(root)
        for dep_name in dependency_names:
            if state.get(dep_name):
                continue
            dep_file = self._find_file_case_insensitive(mods_dir, dep_name)
            if dep_file:
                try:
                    dep_file.unlink(missing_ok=True)
                except Exception:
                    pass

    def _version_entry(self, project_id: str, meta: dict, version) -> dict[str, str]:
        return {
            "title": str((meta or {}).get("title") or project_id),
            "project_id": str(project_id),
            "current_filename": str((meta or {}).get("filename", "")),
            "filename": str(getattr(version, "filename", "") or ""),
            "version_id": str(getattr(version, "version_id", "") or ""),
            "version_number": str(getattr(version, "version_number", "") or ""),
            "download_url": str(getattr(version, "download_url", "") or ""),
        }

    def _incompatible_entry(
        self, project_id: str, meta: dict, target_mc_version: str, kind_label: str
    ) -> dict[str, str]:
        return {
            "title": str((meta or {}).get("title") or project_id),
            "filename": str((meta or {}).get("filename", "")),
            "project_id": str(project_id),
            "reason": _("No Modrinth {} release for Minecraft {}").format(kind_label, target_mc_version),
        }

    def scan_update_compatibility(self, server_id: str, target_mc_version: str) -> dict:
        """Return compatible/incompatible tracked content for a target Minecraft version."""
        info = self._servers.get(server_id)
        if not info:
            empty = {"mods": [], "modpacks": [], "datapacks": []}
            return {"compatible": empty.copy(), "incompatible": empty.copy(), "unknown": empty.copy()}

        from hosty.shared.backend import modrinth_client

        root = info.server_dir
        plan = {
            "compatible": {"mods": [], "modpacks": [], "datapacks": []},
            "incompatible": {"mods": [], "modpacks": [], "datapacks": []},
            "unknown": {"mods": [], "modpacks": [], "datapacks": []},
        }

        def best_version(project_id: str, kind: str):
            try:
                versions = modrinth_client.get_project_versions(project_id)
            except Exception:
                return None
            if not versions:
                return None
            if kind == "datapacks":
                candidates = [v for v in versions if not (v.loaders or [])]
            elif kind == "modpacks":
                loader_candidates = [v for v in versions if "fabric" in [x.lower() for x in (v.loaders or [])]]
                candidates = loader_candidates or versions
            else:
                candidates = [v for v in versions if "fabric" in [x.lower() for x in (v.loaders or [])]]
            exact = [v for v in candidates if target_mc_version in (v.game_versions or [])]
            return exact[0] if exact else False

        for project_id, meta in self._tracked_modpack_state(root).items():
            if not isinstance(meta, dict):
                continue
            version = best_version(str(project_id), "modpacks")
            if version is False:
                entry = self._incompatible_entry(str(project_id), meta, target_mc_version, "modpack")
                entry["filename"] = ", ".join([str(Path(str(f)).name) for f in (meta.get("mods") or [])])
                plan["incompatible"]["modpacks"].append(entry)
            elif version is None:
                plan["unknown"]["modpacks"].append(
                    self._incompatible_entry(str(project_id), meta, target_mc_version, "modpack")
                )
            else:
                entry = self._version_entry(str(project_id), meta, version)
                entry["previous_mods"] = json.dumps([str(Path(str(f)).name) for f in (meta.get("mods") or [])])
                plan["compatible"]["modpacks"].append(entry)

        for project_id, meta in self._tracked_mod_state(root).items():
            if not isinstance(meta, dict):
                continue
            version = best_version(str(project_id), "mods")
            if version is False:
                plan["incompatible"]["mods"].append(
                    self._incompatible_entry(str(project_id), meta, target_mc_version, "mod")
                )
            elif version is None:
                plan["unknown"]["mods"].append(
                    self._incompatible_entry(str(project_id), meta, target_mc_version, "mod")
                )
            else:
                plan["compatible"]["mods"].append(self._version_entry(str(project_id), meta, version))

        for project_id, meta in self._tracked_datapack_state(root).items():
            if not isinstance(meta, dict):
                continue
            version = best_version(str(project_id), "datapacks")
            if version is False:
                plan["incompatible"]["datapacks"].append(
                    self._incompatible_entry(str(project_id), meta, target_mc_version, "datapack")
                )
            elif version is None:
                plan["unknown"]["datapacks"].append(
                    self._incompatible_entry(str(project_id), meta, target_mc_version, "datapack")
                )
            else:
                plan["compatible"]["datapacks"].append(self._version_entry(str(project_id), meta, version))

        return plan

    def _find_file_case_insensitive(self, directory: Path, filename: str) -> Path | None:
        name = Path(str(filename or "")).name
        if not name:
            return None
        direct = directory / name
        if direct.exists():
            return direct
        if not directory.is_dir():
            return None
        for item in directory.iterdir():
            if item.name.casefold() == name.casefold():
                return item
        return None

    def _remove_filename_from_tracked_mods(self, root: Path, filename: str) -> None:
        name = Path(str(filename or "")).name.casefold()
        if not name:
            return

        mods = self._tracked_mod_state(root)
        kept_mods = {
            pid: meta
            for pid, meta in mods.items()
            if Path(str((meta or {}).get("filename", ""))).name.casefold() != name
        }
        if kept_mods != mods:
            self._write_json_file(root / ".hosty-mod-installs.json", {"mods": kept_mods})

        packs = self._tracked_modpack_state(root)
        changed = False
        for meta in packs.values():
            if not isinstance(meta, dict):
                continue
            old_mods = meta.get("mods") or []
            new_mods = [m for m in old_mods if Path(str(m)).name.casefold() != name]
            if new_mods != old_mods:
                meta["mods"] = new_mods
                changed = True
        if changed:
            self._write_json_file(root / ".hosty-modpacks.json", {"installed_projects": packs})

    def apply_compatible_component_updates(
        self, server_id: str, target_mc_version: str, plan: dict | None = None
    ) -> tuple[int, int]:
        """Download compatible target-version Modrinth files and update Hosty tracking."""
        info = self._servers.get(server_id)
        if not info:
            return 0, 0

        from hosty.shared.backend import modrinth_client

        root = info.server_dir
        mods_dir = root / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        dp_dir = self._server_datapacks_dir(root)
        dp_dir.mkdir(parents=True, exist_ok=True)
        plan = plan or self.scan_update_compatibility(server_id, target_mc_version)
        compatible = plan.get("compatible") if isinstance(plan.get("compatible"), dict) else {}
        applied = 0
        failed = 0

        pack_state = self._tracked_modpack_state(root)
        for entry in compatible.get("modpacks", []) or []:
            try:
                project_id = str(entry.get("project_id", "")).strip()
                version_id = str(entry.get("version_id", "")).strip()
                if not project_id or not version_id:
                    continue
                previous_mods = set()
                try:
                    previous_mods = {
                        Path(str(m)).name.casefold()
                        for m in json.loads(str(entry.get("previous_mods") or "[]"))
                        if str(m).strip().lower().endswith(".jar")
                    }
                except Exception:
                    previous_mods = {
                        Path(str(m)).name.casefold()
                        for m in ((pack_state.get(project_id) or {}).get("mods") or [])
                        if str(m).strip().lower().endswith(".jar")
                    }
                result = modrinth_client.install_modpack(version_id, root)
                new_mods = {
                    Path(str(m)).name.casefold()
                    for m in (result.managed_mod_files or [])
                    if str(m).strip().lower().endswith(".jar")
                }
                for removed in previous_mods - new_mods:
                    old = self._find_file_case_insensitive(mods_dir, removed)
                    if old:
                        old.unlink(missing_ok=True)
                    self._remove_filename_from_tracked_mods(root, removed)
                pack_state[project_id] = {
                    "version_id": version_id,
                    "version_number": str(entry.get("version_number", "")),
                    "title": str(entry.get("title", "")),
                    "mods": sorted(new_mods),
                }
                self._write_json_file(root / ".hosty-modpacks.json", {"installed_projects": pack_state})
                applied += 1
            except Exception:
                failed += 1

        managed_mods = set()
        for pack in self._tracked_modpack_state(root).values():
            if isinstance(pack, dict):
                managed_mods.update(Path(str(m)).name.casefold() for m in (pack.get("mods") or []))

        mod_state = self._tracked_mod_state(root)
        for entry in compatible.get("mods", []) or []:
            try:
                project_id = str(entry.get("project_id", "")).strip()
                version_id = str(entry.get("version_id", "")).strip()
                filename = Path(str(entry.get("filename", ""))).name
                download_url = str(entry.get("download_url", "")).strip()
                if not project_id or not version_id or not filename or not download_url:
                    continue

                deps = modrinth_client.resolve_required_dependencies(version_id, target_mc_version, "fabric")
                for dep in deps:
                    dep_name = Path(str(dep.filename)).name
                    if dep_name.casefold() in managed_mods or dep_name.casefold() == filename.casefold():
                        continue
                    modrinth_client.download_to(dep.download_url, mods_dir / dep_name)
                    dep_project_id = str(getattr(dep, "project_id", "") or "").strip()
                    if dep_project_id:
                        mod_state[dep_project_id] = {
                            "title": str(
                                getattr(dep, "title", "") or getattr(dep, "name", "") or dep_project_id
                            ).strip(),
                            "version_id": str(getattr(dep, "version_id", "") or "").strip(),
                            "version_number": str(getattr(dep, "version_number", "") or "").strip(),
                            "filename": dep_name,
                        }

                modrinth_client.download_to(download_url, mods_dir / filename)
                old_filename = str(entry.get("current_filename", "")).strip()
                old_dep_names, new_dep_names = self._replace_mod_dependency_parent(
                    root,
                    old_filename,
                    filename,
                    [dep for dep in deps if Path(str(dep.filename)).name.casefold() not in managed_mods],
                )
                if old_filename and Path(old_filename).name.casefold() != filename.casefold():
                    old = self._find_file_case_insensitive(mods_dir, old_filename)
                    if old:
                        old.unlink(missing_ok=True)
                    self._remove_filename_from_tracked_mods(root, old_filename)
                self._remove_orphaned_dependency_files(root, old_dep_names - new_dep_names)
                mod_state[project_id] = {
                    "title": str(entry.get("title", "")),
                    "version_id": version_id,
                    "version_number": str(entry.get("version_number", "")),
                    "filename": filename,
                }
                self._write_json_file(root / ".hosty-mod-installs.json", {"mods": mod_state})
                applied += 1
            except Exception:
                failed += 1

        dp_state = self._tracked_datapack_state(root)
        for entry in compatible.get("datapacks", []) or []:
            try:
                project_id = str(entry.get("project_id", "")).strip()
                version_id = str(entry.get("version_id", "")).strip()
                filename = Path(str(entry.get("filename", ""))).name
                download_url = str(entry.get("download_url", "")).strip()
                if not project_id or not version_id or not filename or not download_url:
                    continue
                modrinth_client.download_to(download_url, dp_dir / filename)
                old_filename = str(entry.get("current_filename", "")).strip()
                if old_filename and Path(old_filename).name.casefold() != filename.casefold():
                    old = self._find_file_case_insensitive(dp_dir, old_filename)
                    if old:
                        old.unlink(missing_ok=True)
                dp_state[project_id] = {
                    "title": str(entry.get("title", "")),
                    "version_id": version_id,
                    "version_number": str(entry.get("version_number", "")),
                    "filename": filename,
                }
                self._write_json_file(root / ".hosty-datapack-installs.json", {"datapacks": dp_state})
                applied += 1
            except Exception:
                failed += 1

        return applied, failed

    def isolate_incompatible_components(
        self,
        server_id: str,
        target_mc_version: str,
        plan: dict | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        """Move tracked Modrinth mods/modpack files/datapacks that lack a compatible target version."""
        info = self._servers.get(server_id)
        if not info:
            return {"mods": [], "modpacks": [], "datapacks": []}

        root = info.server_dir
        mods_dir = root / "mods"
        datapacks_dir = self._server_datapacks_dir(root)
        disabled_mods = root / "mods_incompatible"
        disabled_datapacks = root / "datapacks_incompatible"
        plan = plan or self.scan_update_compatibility(server_id, target_mc_version)
        incompatible = plan.get("incompatible") if isinstance(plan.get("incompatible"), dict) else {}
        record: dict[str, list[dict[str, str]]] = {"mods": [], "modpacks": [], "datapacks": []}

        mod_state_path = root / ".hosty-mod-installs.json"
        mods = self._tracked_mod_state(root)
        kept_mods = dict(mods)
        for entry in incompatible.get("mods", []) or []:
            project_id = str(entry.get("project_id", "")).strip()
            meta = mods.get(project_id)
            if not isinstance(meta, dict):
                continue
            moved = self._move_if_present(mods_dir, str(meta.get("filename", "")), disabled_mods)
            if moved:
                kept_mods.pop(project_id, None)
                new_entry = dict(entry)
                new_entry["filename"] = moved.name
                record["mods"].append(new_entry)
        if kept_mods != mods:
            self._write_json_file(mod_state_path, {"mods": kept_mods})

        pack_state_path = root / ".hosty-modpacks.json"
        packs = self._tracked_modpack_state(root)
        kept_packs = dict(packs)
        for entry in incompatible.get("modpacks", []) or []:
            project_id = str(entry.get("project_id", "")).strip()
            meta = packs.get(project_id)
            if not isinstance(meta, dict):
                continue
            moved_any = False
            for filename in meta.get("mods") or []:
                moved = self._move_if_present(mods_dir, str(filename), disabled_mods)
                moved_any = bool(moved) or moved_any
            if moved_any:
                kept_packs.pop(project_id, None)
                record["modpacks"].append(dict(entry))
        if kept_packs != packs:
            self._write_json_file(pack_state_path, {"installed_projects": kept_packs})

        dp_state_path = root / ".hosty-datapack-installs.json"
        datapacks = self._tracked_datapack_state(root)
        kept_datapacks = dict(datapacks)
        for entry in incompatible.get("datapacks", []) or []:
            project_id = str(entry.get("project_id", "")).strip()
            meta = datapacks.get(project_id)
            if not isinstance(meta, dict):
                continue
            moved = self._move_if_present(datapacks_dir, str(meta.get("filename", "")), disabled_datapacks)
            if moved:
                kept_datapacks.pop(project_id, None)
                new_entry = dict(entry)
                new_entry["filename"] = moved.name
                record["datapacks"].append(new_entry)
        if kept_datapacks != datapacks:
            self._write_json_file(dp_state_path, {"datapacks": kept_datapacks})

        if any(record.values()):
            previous = self.get_incompatible_components(server_id)
            merged = {key: [*previous.get(key, []), *record.get(key, [])] for key in ("mods", "modpacks", "datapacks")}
            self._write_json_file(root / ".hosty-incompatible-components.json", merged)
        return record

    def get_incompatible_components(self, server_id: str) -> dict[str, list[dict[str, str]]]:
        info = self._servers.get(server_id)
        if not info:
            return {"mods": [], "modpacks": [], "datapacks": []}
        data = self._json_file(info.server_dir / ".hosty-incompatible-components.json")
        out: dict[str, list[dict[str, str]]] = {}
        for key in ("mods", "modpacks", "datapacks"):
            values = data.get(key) if isinstance(data.get(key), list) else []
            out[key] = [v for v in values if isinstance(v, dict)]
        return out

    def delete_incompatible_component(
        self,
        server_id: str,
        kind: str,
        project_id: str = "",
        filename: str = "",
    ) -> tuple[bool, str]:
        """Delete a file moved aside during version update and remove its disabled record."""
        info = self._servers.get(server_id)
        if not info:
            return False, _("Server not found")

        key = str(kind or "").strip().lower()
        aliases = {
            "mod": "mods",
            "mods": "mods",
            "modpack": "modpacks",
            "modpacks": "modpacks",
            "datapack": "datapacks",
            "datapacks": "datapacks",
        }
        key = aliases.get(key, key)
        if key not in {"mods", "modpacks", "datapacks"}:
            return False, _("Unknown disabled item type")

        root = info.server_dir
        disabled_dir = root / ("datapacks_incompatible" if key == "datapacks" else "mods_incompatible")
        data_path = root / ".hosty-incompatible-components.json"
        data = self.get_incompatible_components(server_id)
        records = data.get(key, [])
        project_id = str(project_id or "").strip()
        filename = str(filename or "").strip()

        removed_records: list[dict[str, str]] = []
        kept: list[dict[str, str]] = []
        for record in records:
            rec_project = str(record.get("project_id") or "").strip()
            rec_filename = str(record.get("filename") or "").strip()
            project_matches = bool(project_id) and rec_project == project_id
            filename_matches = bool(filename) and rec_filename == filename
            if project_matches or filename_matches:
                removed_records.append(record)
            else:
                kept.append(record)

        if not removed_records:
            return False, _("Disabled item not found")

        deleted_files = 0
        for record in removed_records:
            names = [filename] if filename else []
            rec_filename = str(record.get("filename") or "").strip()
            if rec_filename:
                names.extend([part.strip() for part in rec_filename.split(",") if part.strip()])
            for name in {Path(n).name for n in names if n}:
                target = self._find_file_case_insensitive(disabled_dir, name)
                if target and target.exists():
                    target.unlink(missing_ok=True)
                    deleted_files += 1

        data[key] = kept
        self._write_json_file(data_path, data)
        self.emit_on_main_thread("server-changed", server_id)
        if deleted_files:
            return True, _("Deleted {} disabled file(s).").format(deleted_files)
        return True, _("Removed disabled item record.")

    @staticmethod
    def backup_game_version(zip_path: Path) -> str:
        name = Path(zip_path).name
        match = re.match(r"^hosty-full-backup-(.+)-\d{8}-\d{6}\.zip$", name)
        return match.group(1) if match else ""

    @staticmethod
    def is_version_older(candidate: str, current: str) -> bool:
        def parse(value: str) -> tuple[int, ...] | None:
            parts = re.findall(r"\d+", str(value or ""))
            if not parts:
                return None
            return tuple(int(p) for p in parts[:4])

        a = parse(candidate)
        b = parse(current)
        if not a or not b:
            return False
        max_len = max(len(a), len(b))
        return a + (0,) * (max_len - len(a)) < b + (0,) * (max_len - len(b))

    def restore_server(self, server_data: dict) -> bool:
        """Restore a previously deleted server metadata entry."""
        try:
            info = ServerInfo(server_data)
        except Exception:
            return False

        if not info.id or info.id in self._servers:
            return False

        self._servers[info.id] = info
        self._save()
        self.emit_on_main_thread("server-added", info.id)
        return True

    def delete_server(self, server_id: str, delete_files: bool = True):
        """Delete a server. Optionally delete its files."""
        info = self._servers.get(server_id)
        if not info:
            return

        if self.playit_manager.is_running_for(server_id):
            self.playit_manager.stop_server(server_id)

        # Stop if running
        process = self._processes.get(server_id)
        if process and process.is_running:
            process.kill()

        if server_id in self._processes:
            del self._processes[server_id]

        # Delete files
        if delete_files and info.server_dir.exists():
            shutil.rmtree(info.server_dir, ignore_errors=True)

        del self._servers[server_id]
        self._save()
        self.emit_on_main_thread("server-removed", server_id)

    def get_process(self, server_id: str) -> ServerProcess | None:
        """Get or create a ServerProcess for a server."""
        info = self._servers.get(server_id)
        if not info:
            return None

        if server_id not in self._processes:
            java_path = self.java_manager.get_java_for_mc(info.mc_version)
            if not java_path:
                # Try system java as fallback
                java_path = shutil.which("java")

            config = self.get_config(server_id)
            max_players = 20
            if config:
                config.load()
                max_players = config.get_int("max-players", 20)

            self._processes[server_id] = ServerProcess(
                server_dir=str(info.server_dir),
                java_path=java_path or "java",
                ram_mb=info.ram_mb,
                max_players=max_players,
            )

        return self._processes[server_id]

    def get_existing_process(self, server_id: str) -> ServerProcess | None:
        """Get an existing ServerProcess without creating a new one."""
        return self._processes.get(server_id)

    def get_config(self, server_id: str) -> ConfigManager | None:
        """Get a ConfigManager for a server's server.properties."""
        info = self._servers.get(server_id)
        if not info:
            return None
        return ConfigManager(str(info.server_dir))

    def is_any_server_running(self) -> bool:
        """Check if any server is currently running."""
        return any(p.is_running for p in self._processes.values())

    def get_running_server_ids(self) -> list[str]:
        """Return all server ids whose processes are running."""
        return [sid for sid, p in self._processes.items() if p.is_running]

    def get_running_server_id(self) -> str | None:
        """Return the first running server id, or None."""
        for server_id, process in self._processes.items():
            if process.is_running:
                return server_id
        return None

    def get_used_ports(self) -> set[int]:
        """Return ports in use by all servers' server.properties."""
        ports: set[int] = set()
        for sid, info in self._servers.items():
            try:
                cfg = self.get_config(sid)
                if cfg:
                    cfg.load()
                    port = cfg.get_int("server-port", 25565)
                    if 1024 <= port <= 65535:
                        ports.add(port)
            except Exception:
                pass
        return ports

    def get_used_bedrock_ports(self) -> set[int]:
        """Return the set of bedrock ports configured across all servers."""
        ports: set[int] = set()
        for sid, info in self._servers.items():
            try:
                cfg = load_playit_config(info.server_dir)
                port = int(cfg.get("bedrock_port", 19132))
                if 1024 <= port <= 65535:
                    ports.add(port)
            except Exception:
                pass
        return ports

    def get_used_voicechat_ports(self) -> set[int]:
        """Return the set of voicechat ports configured across all servers."""
        ports: set[int] = set()
        for sid, info in self._servers.items():
            try:
                cfg = load_playit_config(info.server_dir)
                port = int(cfg.get("voicechat_port", 24454))
                if 1024 <= port <= 65535:
                    ports.add(port)
            except Exception:
                pass
        return ports

    def get_next_available_bedrock_port(self) -> int:
        """Find the next unused bedrock port starting from 19132."""
        used = self.get_used_bedrock_ports()
        port = 19132
        while port in used:
            port += 1
        return port

    def get_next_available_voicechat_port(self) -> int:
        """Find the next unused voicechat port starting from 24454."""
        used = self.get_used_voicechat_ports()
        port = 24454
        while port in used:
            port += 1
        return port

    def get_next_available_port(self, base: int = 25565) -> int:
        """Find the next unused port starting from base."""
        used = self.get_used_ports()
        port = base
        while port in used:
            port += 1
        return port

    def set_java_port(self, server_id: str, port: int) -> None:
        """Set the server port in server.properties."""
        info = self._servers.get(server_id)
        if not info:
            return
        cfg = self.get_config(server_id)
        if cfg:
            cfg.load()
            cfg.set_value("server-port", port)
            cfg.save()

    def get_bedrock_port(self, server_id: str) -> int:
        """Read the bedrock port from the server's playit config."""
        info = self._servers.get(server_id)
        if not info:
            return 19132
        try:
            cfg = load_playit_config(info.server_dir)
            return int(cfg.get("bedrock_port", 19132))
        except Exception:
            return 19132

    def get_voicechat_port(self, server_id: str) -> int:
        """Read the voicechat port from the server's playit config."""
        info = self._servers.get(server_id)
        if not info:
            return 24454
        try:
            cfg = load_playit_config(info.server_dir)
            return int(cfg.get("voicechat_port", 24454))
        except Exception:
            return 24454

    def set_bedrock_port(self, server_id: str, port: int) -> None:
        """Set the bedrock port in the server's playit config."""
        info = self._servers.get(server_id)
        if not info:
            return
        from hosty.shared.backend.playit_config import save_playit_config

        cfg = load_playit_config(info.server_dir)
        cfg["bedrock_port"] = port
        save_playit_config(info.server_dir, cfg)

    def set_voicechat_port(self, server_id: str, port: int) -> None:
        """Set the voicechat port in the server's playit config."""
        info = self._servers.get(server_id)
        if not info:
            return
        from hosty.shared.backend.playit_config import save_playit_config

        cfg = load_playit_config(info.server_dir)
        cfg["voicechat_port"] = port
        save_playit_config(info.server_dir, cfg)

    def check_bedrock_port_conflict(self, server_id: str) -> int | None:
        """Return the port if another running server uses the same bedrock port."""
        port = self.get_bedrock_port(server_id)
        if not self.has_bedrock_tunnel(server_id):
            for sid in self._servers:
                if sid != server_id and self.has_bedrock_tunnel(sid):
                    break
            else:
                return None
        for sid, info in self._servers.items():
            if sid == server_id:
                continue
            proc = self._processes.get(sid)
            if not proc or not proc.is_running:
                continue
            if self.get_bedrock_port(sid) == port:
                return port
        return None

    def check_voicechat_port_conflict(self, server_id: str) -> int | None:
        """Return the port if another running server uses the same voicechat port."""
        port = self.get_voicechat_port(server_id)
        if not self.has_voicechat_tunnel(server_id):
            for sid in self._servers:
                if sid != server_id and self.has_voicechat_tunnel(sid):
                    break
            else:
                return None
        for sid, info in self._servers.items():
            if sid == server_id:
                continue
            proc = self._processes.get(sid)
            if not proc or not proc.is_running:
                continue
            if self.get_voicechat_port(sid) == port:
                return port
        return None

    def resolve_playit_port_conflicts(self, server_id: str) -> None:
        """No-op: port conflicts are no longer auto-resolved."""

    def has_bedrock_tunnel(self, server_id: str) -> bool:
        """Check if a server has a bedrock tunnel configured."""
        info = self._servers.get(server_id)
        if not info:
            return False
        try:
            cfg = load_playit_config(info.server_dir)
            return bool(str(cfg.get("bedrock_endpoint", "")).strip())
        except Exception:
            return False

    def has_voicechat_tunnel(self, server_id: str) -> bool:
        """Check if a server has a voicechat tunnel configured."""
        info = self._servers.get(server_id)
        if not info:
            return False
        try:
            cfg = load_playit_config(info.server_dir)
            return bool(str(cfg.get("voicechat_endpoint", "")).strip())
        except Exception:
            return False

    def check_port_conflict(self, server_id: str) -> int | None:
        """Return the conflicting port if another running server uses this server's port, else None."""
        info = self._servers.get(server_id)
        if not info:
            return None
        cfg = self.get_config(server_id)
        if not cfg:
            return None
        cfg.load()
        my_port = cfg.get_int("server-port", 25565)
        for sid, other in self._servers.items():
            if sid == server_id:
                continue
            proc = self._processes.get(sid)
            if not proc or not proc.is_running:
                continue
            try:
                other_cfg = self.get_config(sid)
                if not other_cfg:
                    continue
                other_cfg.load()
                other_port = other_cfg.get_int("server-port", 25565)
                if other_port == my_port:
                    return my_port
            except Exception:
                pass
        return None

    def begin_mod_operation(self, server_id: str) -> None:
        """Mark a server as having an active mod install/update operation."""
        if not server_id:
            return
        count = int(self._mods_operation_counts.get(server_id, 0)) + 1
        self._mods_operation_counts[server_id] = count
        self.emit_on_main_thread("mods-operation-changed", server_id, True, count)

    def end_mod_operation(self, server_id: str) -> None:
        """Clear one active mod install/update operation for a server."""
        if not server_id:
            return
        count = int(self._mods_operation_counts.get(server_id, 0)) - 1
        if count <= 0:
            self._mods_operation_counts.pop(server_id, None)
            count = 0
            active = False
        else:
            self._mods_operation_counts[server_id] = count
            active = True
        self.emit_on_main_thread("mods-operation-changed", server_id, active, count)

    def is_mod_operation_active(self, server_id: str) -> bool:
        """True while any mod install/update operation is active for this server."""
        if not server_id:
            return False
        return int(self._mods_operation_counts.get(server_id, 0)) > 0

    def stop_all(self):
        """Stop all running servers."""
        self.playit_manager.stop()
        for server_id, process in self._processes.items():
            if process.is_running:
                process.stop()
                # Wait up to 3 seconds for graceful shutdown, then forcefully kill to prevent orphans
                try:
                    process.process.wait(timeout=3.0)
                except Exception:
                    pass
                process.kill()

    def _configured_level_name(self, server_root: Path) -> str:
        """Read level-name from server.properties, defaulting to world."""
        try:
            cfg = ConfigManager(server_root)
            cfg.load()
            name = cfg.get("level-name", "world").strip()
            return name or "world"
        except Exception:
            return "world"

    def _is_world_dir(self, item: Path, level_name: str) -> bool:
        if not item.is_dir():
            return False

        if (item / "level.dat").exists():
            return True

        if item.name.casefold() == level_name.casefold():
            return True

        markers = (
            "region",
            "data",
            "playerdata",
            "poi",
            "entities",
            "stats",
            "advancements",
            "dimensions",
            "DIM-1",
            "DIM1",
            "session.lock",
            "uid.dat",
        )
        return any((item / marker).exists() for marker in markers)

    def _is_importable_world_dir(self, item: Path) -> bool:
        if not item.is_dir():
            return False
        if not (item / "level.dat").is_file():
            return False

        markers = (
            "region",
            "data",
            "playerdata",
            "poi",
            "entities",
            "stats",
            "advancements",
            "dimensions",
            "DIM-1",
            "DIM1",
            "session.lock",
            "uid.dat",
        )
        return any((item / marker).exists() for marker in markers)

    def _iter_world_dirs(self, server_root: Path) -> list[Path]:
        if not server_root.is_dir():
            return []

        level_name = self._configured_level_name(server_root)
        preferred = server_root / "world"
        if self._is_world_dir(preferred, level_name):
            return [preferred]

        worlds = [item for item in server_root.iterdir() if self._is_world_dir(item, level_name)]
        if not worlds:
            return []

        worlds = sorted(worlds, key=lambda p: p.name.lower())
        return [worlds[0]]

    def _unique_world_destination(self, server_root: Path, name: str) -> Path:
        safe_name = Path(name).name.strip() or "world"
        candidate = server_root / safe_name
        if not candidate.exists():
            return candidate

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = server_root / f"{safe_name}-{stamp}"
        suffix = 2
        while candidate.exists():
            candidate = server_root / f"{safe_name}-{stamp}-{suffix}"
            suffix += 1
        return candidate

    def create_world_folder(self, server_id: str, name: str, seed: str = "", level_type: str = "") -> tuple[bool, str]:
        """Create/select a new world folder and configure the server to generate it."""
        info = self.get_server(server_id)
        if not info:
            return False, _("Server not found")

        process = self._processes.get(server_id)
        if process and process.is_running:
            return False, _("Server is running")

        root = info.server_dir
        world_dir = root / "world"

        try:
            level_name = self._configured_level_name(root)
            for item in root.iterdir():
                if not self._is_world_dir(item, level_name):
                    continue
                if item.resolve() == world_dir.resolve():
                    continue
                if item.exists():
                    shutil.rmtree(item, ignore_errors=True)

            if world_dir.exists():
                shutil.rmtree(world_dir, ignore_errors=True)

            world_dir.mkdir(parents=True, exist_ok=True)
            cfg = ConfigManager(root)
            cfg.load()
            cfg.set_value("level-name", "world")
            cfg.set_value("level-seed", seed.strip())
            if level_type:
                cfg.set_value("level-type", level_type)
            cfg.save()
        except Exception as e:
            return False, str(e)

        self.emit_on_main_thread("server-changed", server_id)
        return True, world_dir.name

    def import_world_folder(self, server_id: str, source: str | Path) -> tuple[bool, str]:
        """Copy an existing world folder into a server and select it as level-name."""
        info = self.get_server(server_id)
        if not info:
            return False, _("Server not found")

        process = self._processes.get(server_id)
        if process and process.is_running:
            return False, _("Server is running")

        src = Path(source).expanduser()
        if not src.is_dir():
            return False, _("Selected world folder does not exist")
        if not self._is_importable_world_dir(src):
            return False, _("Selected folder does not look like a Minecraft world")

        root = info.server_dir
        dst = root / "world"
        try:
            level_name = self._configured_level_name(root)
            for item in root.iterdir():
                if not self._is_world_dir(item, level_name):
                    continue
                if item.resolve() == dst.resolve():
                    continue
                if item.exists():
                    shutil.rmtree(item, ignore_errors=True)

            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)

            from hosty.shared.utils.nbt_utils import get_world_info

            seed, wtype = get_world_info(src)

            shutil.copytree(src, dst)
            cfg = ConfigManager(root)
            cfg.load()
            cfg.set_value("level-name", "world")
            if seed:
                cfg.set_value("level-seed", seed)
            else:
                cfg.set_value("level-seed", "")
            if wtype:
                cfg.set_value("level-type", wtype)
            cfg.save()
        except Exception as e:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            return False, str(e)

        self.emit_on_main_thread("server-changed", server_id)
        return True, dst.name

    def export_world_zip(self, server_id: str, world: str | Path, destination: str | Path) -> tuple[bool, str]:
        """Export one world folder to a zip archive."""
        info = self.get_server(server_id)
        if not info:
            return False, _("Server not found")

        world_path = Path(world)
        if not world_path.is_absolute():
            world_path = info.server_dir / world_path
        if not world_path.is_dir():
            return False, _("World folder does not exist")

        try:
            world_path.resolve().relative_to(info.server_dir.resolve())
        except ValueError:
            return False, _("World folder is outside this server")

        dest = Path(destination).expanduser()
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in world_path.rglob("*"):
                    if not item.is_file():
                        continue
                    arc = Path(world_path.name) / item.relative_to(world_path)
                    zf.write(item, arcname=str(arc).replace("\\", "/"))
        except Exception as e:
            return False, str(e)

        return True, str(dest)

    def create_world_backup(self, server_id: str, auto: bool = False) -> tuple[bool, str]:
        """Create a zip backup containing world folders only."""
        info = self.get_server(server_id)
        if not info:
            return False, _("Server not found")

        process = self._processes.get(server_id)
        if process and process.is_running:
            return False, _("Server is running")

        root = info.server_dir
        if not root.exists():
            return False, _("Server directory does not exist")

        worlds = self._iter_world_dirs(root)
        if not worlds:
            return False, _("No world folder found")

        backups_dir = root / "hosty-backups"
        backups_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = "hosty-auto-backup" if auto else "hosty-backup"
        backup_path = backups_dir / f"{prefix}-{stamp}.zip"

        try:
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                world_dir = worlds[0]
                for item in world_dir.rglob("*"):
                    if not item.is_file():
                        continue
                    arc = item.relative_to(root)
                    zf.write(item, arcname=str(arc).replace("\\", "/"))
        except Exception as e:
            return False, str(e)

        return True, backup_path.name

    def create_full_backup(self, server_id: str) -> tuple[bool, str]:
        """Create a zip backup containing everything in the server directory,
        specifically tailored for version updates."""
        info = self.get_server(server_id)
        if not info:
            return False, _("Server not found")

        process = self._processes.get(server_id)
        if process and process.is_running:
            return False, _("Server is running")

        root = info.server_dir
        if not root.exists():
            return False, _("Server directory does not exist")

        backups_dir = root / "hosty-backups"
        backups_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Tag the backup with the current server version
        version = info.mc_version if info.mc_version else "unknown"
        backup_path = backups_dir / f"hosty-full-backup-{version}-{stamp}.zip"

        try:
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in root.rglob("*"):
                    if not item.is_file():
                        continue
                    # Skip the backups folder itself
                    if hasattr(item, "is_relative_to") and item.is_relative_to(backups_dir):
                        continue
                    elif str(item).startswith(str(backups_dir)):  # fallback
                        continue

                    arc = item.relative_to(root)
                    zf.write(item, arcname=str(arc).replace("\\", "/"))
        except Exception as e:
            return False, str(e)

        return True, backup_path.name

    def restore_world_backup(self, server_id: str, zip_path: Path) -> tuple[bool, str]:
        """Restore a zip backup. If it's a full backup, it overwrites everything
        except backups. Otherwise, it just replaces worlds."""
        import shutil
        import tempfile

        info = self.get_server(server_id)
        if not info:
            return False, _("Server not found")

        process = self._processes.get(server_id)
        if process and process.is_running:
            return False, _("Server is running")

        root = info.server_dir
        if not root.exists():
            return False, _("Server directory does not exist")

        if not zip_path.exists():
            return False, _("Backup file not found")

        is_full = zip_path.name.startswith("hosty-full-backup-")

        try:
            with tempfile.TemporaryDirectory(prefix="hosty-restore-") as td:
                tmp_root = Path(td).resolve()
                with zipfile.ZipFile(zip_path, "r") as zf:
                    for zi in zf.infolist():
                        candidate = (tmp_root / zi.filename).resolve()
                        if hasattr(candidate, "is_relative_to") and not candidate.is_relative_to(tmp_root):
                            return False, _("Backup archive contains invalid paths.")
                        elif not str(candidate).startswith(str(tmp_root)):
                            return False, _("Backup archive contains invalid paths.")
                    zf.extractall(tmp_root)

                if is_full:
                    # Nuke everything in root except hosty-backups, then copy all
                    for item in root.iterdir():
                        if item.name == "hosty-backups":
                            continue
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)

                    for item in tmp_root.iterdir():
                        dst = root / item.name
                        if item.is_dir():
                            shutil.copytree(item, dst, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dst)
                else:
                    # Just restore worlds
                    extracted_worlds = self._iter_world_dirs(tmp_root)
                    if not extracted_worlds:
                        return False, _("This backup does not contain any world data.")

                    level_name = "world"
                    for item in root.iterdir():
                        if not item.is_dir():
                            continue
                        if (
                            (item / "level.dat").exists()
                            or item.name.casefold() == level_name.casefold()
                            or any(
                                (item / marker).exists()
                                for marker in (
                                    "region",
                                    "data",
                                    "playerdata",
                                    "poi",
                                    "entities",
                                    "stats",
                                    "advancements",
                                    "dimensions",
                                    "DIM-1",
                                    "DIM1",
                                    "session.lock",
                                    "uid.dat",
                                )
                            )
                        ):
                            shutil.rmtree(item, ignore_errors=True)

                    for item in extracted_worlds:
                        dst = root / "world"
                        if dst.is_dir():
                            shutil.rmtree(dst, ignore_errors=True)
                        shutil.copytree(item, dst, dirs_exist_ok=True)

            return True, _("Restored.")
        except Exception as e:
            return False, str(e)
