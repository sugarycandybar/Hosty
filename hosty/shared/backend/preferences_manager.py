"""
PreferencesManager - Persist and retrieve Hosty app-level preferences.
"""
from __future__ import annotations

import json
from pathlib import Path

from hosty.shared.utils.constants import DATA_DIR, DEFAULT_RAM_MB, MIN_RAM_MB, MAX_RAM_MB


SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "default_ram_mb": DEFAULT_RAM_MB,
    "run_in_background_on_close": False,
    "open_on_startup": False,
    "prevent_sleep_while_running": False,
    "auto_backup_on_stop": True,
    "auto_resolve_mod_dependencies": True,
    "theme": "system",
}


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
            with open(self._settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._settings.update(data)
        except Exception:
            # Fall back to defaults on malformed settings.
            self._settings = dict(DEFAULT_SETTINGS)

    def _save(self) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._settings_path, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2)

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
