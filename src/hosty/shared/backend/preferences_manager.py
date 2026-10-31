"""
PreferencesManager - Persist and retrieve Hosty app-level preferences.
"""

from __future__ import annotations

import json
from pathlib import Path

from hosty.shared.utils.constants import DATA_DIR, DEFAULT_RAM_MB, MAX_RAM_MB, MIN_RAM_MB
from hosty.shared.utils.file_utils import atomic_write_json

SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "default_ram_mb": DEFAULT_RAM_MB,
    "run_in_background_on_close": False,
    "open_on_startup": False,
    "remote_management_enabled": False,
    "prevent_sleep_while_running": False,
    "auto_backup_on_stop": True,
    "auto_backup_scope": "world",
    "backup_retention_days": 30,
    "max_backups": 0,
    "auto_resolve_mod_dependencies": True,
    "theme": "system",
    "language": "system",
}

#: Selectable backup age limits in days (0 = keep forever).
BACKUP_RETENTION_OPTIONS = (0, 7, 14, 30, 60, 90)

#: Upper bound for the "maximum backups" setting (0 = unlimited).
MAX_BACKUPS_LIMIT = 1000


class PreferencesManager:
    """Lightweight JSON-backed settings store."""

    def __init__(self, settings_path: Path = SETTINGS_FILE):
        self._settings_path = settings_path
        self._settings = dict(DEFAULT_SETTINGS)
        self._load()

    def _load(self) -> None:
        if not self._settings_path.exists():
            return
        try:
            with open(self._settings_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._settings.update(data)
                self._migrate_legacy_backup_toggle(data)
        except Exception:
            # Fall back to defaults on malformed settings.
            self._settings = dict(DEFAULT_SETTINGS)

    def _migrate_legacy_backup_toggle(self, file_data: dict) -> None:
        """One-time upgrade of the old on/off toggle (True was 30 days)."""
        if "backup_retention_days" in file_data or "auto_delete_old_backups" not in file_data:
            return
        legacy = file_data.get("auto_delete_old_backups", True)
        if legacy is False or str(legacy).strip().lower() in ("false", "no", "off", ""):
            self._settings["backup_retention_days"] = 0
        else:
            self._settings["backup_retention_days"] = 30
        self._settings.pop("auto_delete_old_backups", None)
        self._save()

    def _save(self) -> None:
        try:
            atomic_write_json(self._settings_path, self._settings)
        except Exception:
            pass

    @property
    def default_ram_mb(self) -> int:
        raw = int(self._settings.get("default_ram_mb", DEFAULT_RAM_MB))
        return max(MIN_RAM_MB, min(MAX_RAM_MB, raw))

    @default_ram_mb.setter
    def default_ram_mb(self, value: int) -> None:
        clamped = max(MIN_RAM_MB, min(MAX_RAM_MB, int(value)))
        self._settings["default_ram_mb"] = clamped
        self._save()

    @property
    def run_in_background_on_close(self) -> bool:
        return bool(self._settings.get("run_in_background_on_close", False))

    @run_in_background_on_close.setter
    def run_in_background_on_close(self, value: bool) -> None:
        self._settings["run_in_background_on_close"] = bool(value)
        self._save()

    @property
    def open_on_startup(self) -> bool:
        return bool(self._settings.get("open_on_startup", False))

    @open_on_startup.setter
    def open_on_startup(self, value: bool) -> None:
        self._settings["open_on_startup"] = bool(value)
        self._save()

    @property
    def remote_management_enabled(self) -> bool:
        return bool(self._settings.get("remote_management_enabled", False))

    @remote_management_enabled.setter
    def remote_management_enabled(self, value: bool) -> None:
        self._settings["remote_management_enabled"] = bool(value)
        self._save()

    @property
    def prevent_sleep_while_running(self) -> bool:
        return bool(self._settings.get("prevent_sleep_while_running", False))

    @prevent_sleep_while_running.setter
    def prevent_sleep_while_running(self, value: bool) -> None:
        self._settings["prevent_sleep_while_running"] = bool(value)
        self._save()

    @property
    def auto_backup_on_stop(self) -> bool:
        return bool(self._settings.get("auto_backup_on_stop", True))

    @auto_backup_on_stop.setter
    def auto_backup_on_stop(self, value: bool) -> None:
        self._settings["auto_backup_on_stop"] = bool(value)
        self._save()

    #: What the stop-triggered auto backup contains.
    AUTO_BACKUP_SCOPES = ("world", "full")

    @property
    def auto_backup_scope(self) -> str:
        """Auto-backup content: ``world`` (world folders) or ``full`` (everything)."""
        scope = str(self._settings.get("auto_backup_scope", "world")).strip().lower()
        return scope if scope in self.AUTO_BACKUP_SCOPES else "world"

    @auto_backup_scope.setter
    def auto_backup_scope(self, value: str) -> None:
        scope = str(value or "").strip().lower()
        self._settings["auto_backup_scope"] = scope if scope in self.AUTO_BACKUP_SCOPES else "world"
        self._save()

    @property
    def backup_retention_days(self) -> int:
        """Max backup age in days (0 = keep forever)."""
        try:
            return max(0, int(self._settings.get("backup_retention_days", 30)))
        except (TypeError, ValueError):
            return 30

    @backup_retention_days.setter
    def backup_retention_days(self, value: int) -> None:
        try:
            days = max(0, int(value))
        except (TypeError, ValueError):
            days = 30
        self._settings["backup_retention_days"] = days
        self._settings.pop("auto_delete_old_backups", None)
        self._save()

    @property
    def max_backups(self) -> int:
        """Max backups kept per server (0 = unlimited, oldest deleted first)."""
        try:
            return max(0, int(self._settings.get("max_backups", 0)))
        except (TypeError, ValueError):
            return 0

    @max_backups.setter
    def max_backups(self, value: int) -> None:
        try:
            count = max(0, min(MAX_BACKUPS_LIMIT, int(value)))
        except (TypeError, ValueError):
            count = 0
        self._settings["max_backups"] = count
        self._save()

    @property
    def auto_resolve_mod_dependencies(self) -> bool:
        return bool(self._settings.get("auto_resolve_mod_dependencies", True))

    @auto_resolve_mod_dependencies.setter
    def auto_resolve_mod_dependencies(self, value: bool) -> None:
        self._settings["auto_resolve_mod_dependencies"] = bool(value)
        self._save()

    @property
    def theme(self) -> str:
        return self._settings.get("theme", "system")

    @theme.setter
    def theme(self, value: str) -> None:
        if value in ("system", "light", "dark"):
            self._settings["theme"] = value
            self._save()

    @property
    def language(self) -> str:
        return self._settings.get("language", "system")

    @language.setter
    def language(self, value: str) -> None:
        self._settings["language"] = value
        self._save()
