"""
Per-server playit configuration helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

from hosty.shared.utils.file_utils import atomic_write_json

DEFAULT_PLAYIT_CONFIG = {
    "secret": "",
    "enabled": False,
    "setup_complete": False,
    "auto_start": True,
    "auto_install": True,
    "java_endpoint": "",
    "bedrock_endpoint": "",
    "voicechat_endpoint": "",
    "bedrock_port": 19132,
    "voicechat_port": 24454,
    "java_tunnel_id": "",
    "bedrock_tunnel_id": "",
    "voicechat_tunnel_id": "",
}

#: Playit tunnel kinds used across the backend and the Connect tab.
TUNNEL_KINDS = ("java", "bedrock", "voicechat")


def _tunnel_id_field(kind: str) -> str:
    return f"{kind}_tunnel_id"


def playit_config_path(server_dir: str | Path) -> Path:
    root = Path(server_dir)
    return root / ".hosty-playit.json"


def load_playit_config(server_dir: str | Path) -> dict:
    path = playit_config_path(server_dir)
    if not path.exists():
        return dict(DEFAULT_PLAYIT_CONFIG)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(DEFAULT_PLAYIT_CONFIG)

    if not isinstance(data, dict):
        return dict(DEFAULT_PLAYIT_CONFIG)

    cfg = dict(DEFAULT_PLAYIT_CONFIG)
    cfg.update(data)
    cfg["secret"] = str(cfg.get("secret") or "")
    cfg["enabled"] = bool(cfg.get("enabled", False))
    cfg["setup_complete"] = bool(cfg.get("setup_complete", False))
    cfg["auto_start"] = bool(cfg.get("auto_start", True))
    cfg["auto_install"] = bool(cfg.get("auto_install", True))
    cfg["java_endpoint"] = str(cfg.get("java_endpoint") or "").strip()
    cfg["bedrock_endpoint"] = str(cfg.get("bedrock_endpoint") or "").strip()
    cfg["voicechat_endpoint"] = str(cfg.get("voicechat_endpoint") or "").strip()
    for kind in TUNNEL_KINDS:
        cfg[_tunnel_id_field(kind)] = str(cfg.get(_tunnel_id_field(kind)) or "").strip()
    try:
        cfg["bedrock_port"] = int(cfg.get("bedrock_port", 19132))
    except Exception:
        cfg["bedrock_port"] = 19132
    try:
        cfg["voicechat_port"] = int(cfg.get("voicechat_port", 24454))
    except Exception:
        cfg["voicechat_port"] = 24454
    return cfg


def save_playit_config(server_dir: str | Path, config: dict) -> bool:
    path = playit_config_path(server_dir)
    payload = dict(DEFAULT_PLAYIT_CONFIG)
    payload.update(config or {})
    payload["secret"] = str(payload.get("secret") or "")
    payload["enabled"] = bool(payload.get("enabled", False))
    payload["setup_complete"] = bool(payload.get("setup_complete", False))
    payload["auto_start"] = bool(payload.get("auto_start", True))
    payload["auto_install"] = bool(payload.get("auto_install", True))
    payload["java_endpoint"] = str(payload.get("java_endpoint") or "").strip()
    payload["bedrock_endpoint"] = str(payload.get("bedrock_endpoint") or "").strip()
    payload["voicechat_endpoint"] = str(payload.get("voicechat_endpoint") or "").strip()
    for kind in TUNNEL_KINDS:
        payload[_tunnel_id_field(kind)] = str(payload.get(_tunnel_id_field(kind)) or "").strip()
    try:
        payload["bedrock_port"] = int(payload.get("bedrock_port", 19132))
    except Exception:
        payload["bedrock_port"] = 19132
    try:
        payload["voicechat_port"] = int(payload.get("voicechat_port", 24454))
    except Exception:
        payload["voicechat_port"] = 24454

    try:
        atomic_write_json(path, payload)
        return True
    except Exception:
        return False
