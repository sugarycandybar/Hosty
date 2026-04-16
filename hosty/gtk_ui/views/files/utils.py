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

__all__ = [
    "_open_uri",
    "_open_path",
    "_world_dirs",
    "_world_dimension_dirs",
    "_is_relative_to",
    "_format_size",
    "_format_mtime",
    "_format_compact_count",
    "_is_descendant_of",
]

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
    def _configured_level_name(root: Path) -> str:
        props = root / "server.properties"
        if not props.exists():
            return "world"
        try:
            with open(props, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "level-name":
                        name = value.strip()
                        return name or "world"
        except Exception:
            pass
        return "world"

    def _is_world_dir(item: Path, level_name: str) -> bool:
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

    out = []
    if not server_root.is_dir():
        return out

    level_name = _configured_level_name(server_root)
    for item in server_root.iterdir():
        if _is_world_dir(item, level_name):
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


