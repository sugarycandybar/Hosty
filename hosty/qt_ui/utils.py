"""Shared utilities for the PySide6 Windows frontend."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor

from hosty.shared.utils.constants import ServerStatus

try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

import ctypes
from ctypes import wintypes


__all__ = [
    '_open_path',
    '_format_uptime',
    '_iter_world_dirs',
    '_status_prefix',
    '_status_color_hex',
    '_MainThreadInvoker',
    'apply_window_theme',
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


def apply_window_theme(
    window_id: int, 
    dark: bool, 
    caption_color: Optional[QColor] = None, 
    text_color: Optional[QColor] = None
) -> None:
    """
    Apply Windows-specific DWM attributes to the window frame.
    Requires Windows 10 1809+ for dark mode, Windows 11 for colors.
    """
    if sys.platform != "win32":
        return

    try:
        dwmapi = ctypes.windll.dwmapi
        hwnd = wintypes.HWND(window_id)

        # 1. Immersive Dark Mode (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
        # Fallback to 19 for older Windows 10 versions if needed, 
        # but 20 is standard for modern Win10/11.
        rendering_policy = 20
        value = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            rendering_policy,
            ctypes.byref(value),
            ctypes.sizeof(value)
        )

        # 2. Caption Color (DWMWA_CAPTION_COLOR = 35) - Windows 11 only
        if caption_color and hasattr(dwmapi, "DwmSetWindowAttribute"):
            # DWM expects 0x00RRGGBB or 0x00BBGGRR depending on version, 
            # usually COLORREF (0x00BBGGRR)
            color_val = (caption_color.blue() << 16) | (caption_color.green() << 8) | caption_color.red()
            color_ref = ctypes.c_int(color_val)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                35,
                ctypes.byref(color_ref),
                ctypes.sizeof(color_ref)
            )

        # 3. Text Color (DWMWA_TEXT_COLOR = 36) - Windows 11 only
        if text_color and hasattr(dwmapi, "DwmSetWindowAttribute"):
            color_val = (text_color.blue() << 16) | (text_color.green() << 8) | text_color.red()
            color_ref = ctypes.c_int(color_val)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                36,
                ctypes.byref(color_ref),
                ctypes.sizeof(color_ref)
            )
            
    except Exception as e:
        print(f"Failed to apply window theme: {e}")
