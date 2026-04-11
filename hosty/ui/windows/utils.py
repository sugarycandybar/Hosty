"""PySide6-based Windows frontend for Hosty."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import psutil

    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

from hosty.backend.config_manager import ConfigManager
from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.core.events import set_main_thread_dispatcher
from hosty.utils.constants import (
    DEFAULT_RAM_MB,
    DEFAULT_SERVER_PROPERTIES,
    MAX_RAM_MB,
    MIN_RAM_MB,
    ServerStatus,
    get_required_java_version,
)



__all__ = [
    '_open_path',
    '_format_uptime',
    '_iter_world_dirs',
    '_status_prefix',
    '_MainThreadInvoker',
]

def _open_path(path: Path) -> bool:
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
        ServerStatus.RUNNING: "[RUNNING]",
        ServerStatus.STARTING: "[STARTING]",
        ServerStatus.STOPPING: "[STOPPING]",
        ServerStatus.STOPPED: "[STOPPED]",
    }
    return mapping.get(status, "[UNKNOWN]")


class _MainThreadInvoker(QObject):
    invoke = Signal(object, object, object)

    def __init__(self) -> None:
        super().__init__()
        self.invoke.connect(self._run)

    @Slot(object, object, object)
    def _run(self, callback, args, kwargs) -> None:
        callback(*args, **kwargs)


