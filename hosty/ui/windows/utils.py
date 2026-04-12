"""Shared utilities for the PySide6 Windows frontend."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor

from hosty.utils.constants import ServerStatus

try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False


__all__ = [
    '_open_path',
    '_format_uptime',
    '_iter_world_dirs',
    '_status_prefix',
    '_status_color_hex',
    '_MainThreadInvoker',
    'HAS_PSUTIL',
]


def _open_path(path: Path) -> bool:
    """Open a folder or file in the system file manager."""
    target = path.resolve()
    if target.is_file():
        target = target.parent
    try:
        os.startfile(str(target))
        return True
    except Exception:
        return False


def _format_uptime(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _iter_world_dirs(server_root: Path) -> list[Path]:
    if not server_root.is_dir():
        return []
    worlds = []
    for item in server_root.iterdir():
        if item.is_dir() and (item / "level.dat").exists():
            worlds.append(item)
    return sorted(worlds, key=lambda p: p.name.lower())


def _status_prefix(status: str) -> str:
    mapping = {
        ServerStatus.RUNNING: "●",
        ServerStatus.STARTING: "◐",
        ServerStatus.STOPPING: "◐",
        ServerStatus.STOPPED: "○",
    }
    return mapping.get(status, "?")


def _status_color_hex(status: str) -> str:
    """Return a hex color for the given server status."""
    mapping = {
        ServerStatus.RUNNING: "#7A9E65",
        ServerStatus.STARTING: "#D4944C",
        ServerStatus.STOPPING: "#D4944C",
        ServerStatus.STOPPED: "#887A6B",
    }
    return mapping.get(status, "#887A6B")


class _MainThreadInvoker(QObject):
    invoke = Signal(object, object, object)

    def __init__(self) -> None:
        super().__init__()
        self.invoke.connect(self._run)

    @Slot(object, object, object)
    def _run(self, callback, args, kwargs) -> None:
        callback(*args, **kwargs)
