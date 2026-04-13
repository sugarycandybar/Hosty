"""
Per-server playit configuration helpers.
"""
from __future__ import annotations

import json
from pathlib import Path


DEFAULT_PLAYIT_CONFIG = {
    "secret": "",
    "enabled": False,
    "setup_complete": False,
    "auto_start": True,
    "auto_install": True,
}


def playit_config_path(server_dir: str | Path) -> Path:
    root = Path(server_dir)
    return root / ".hosty-playit.json"


def load_playit_config(server_dir: str | Path) -> dict:
    path = playit_config_path(server_dir)
    if not path.exists():
        return dict(DEFAULT_PLAYIT_CONFIG)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(DEFAULT_PLAYIT_CONFIG)

    if not isinstance(data, dict):
        return dict(DEFAULT_PLAYIT_CONFIG)

    cfg = dict(DEFAULT_PLAYIT_CONFIG)
    cfg.update(data)
    cfg["secret"] = str(cfg.get("secret", ""))
    cfg["enabled"] = bool(cfg.get("enabled", False))
    cfg["setup_complete"] = bool(cfg.get("setup_complete", False))
    cfg["auto_start"] = bool(cfg.get("auto_start", True))
    cfg["auto_install"] = bool(cfg.get("auto_install", True))
    return cfg


def save_playit_config(server_dir: str | Path, config: dict) -> bool:
    path = playit_config_path(server_dir)
    payload = dict(DEFAULT_PLAYIT_CONFIG)
    payload.update(config or {})
    payload["secret"] = str(payload.get("secret", ""))
    payload["enabled"] = bool(payload.get("enabled", False))
    payload["setup_complete"] = bool(payload.get("setup_complete", False))
    payload["auto_start"] = bool(payload.get("auto_start", True))
    payload["auto_install"] = bool(payload.get("auto_install", True))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception:
        return False
