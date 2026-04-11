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



from ..utils import *
from ..dialogs.create_server import CreateServerDialog

class FilesMixin:
    def _build_files_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)

        btn_row = QHBoxLayout()
        open_server = QPushButton("Open Server Folder", tab)
        open_mods = QPushButton("Open Mods Folder", tab)
        open_server.clicked.connect(self._open_server_folder)
        open_mods.clicked.connect(self._open_mods_folder)
        btn_row.addWidget(open_server)
        btn_row.addWidget(open_mods)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._worlds_label = QLabel("Worlds", tab)
        layout.addWidget(self._worlds_label)

        self._world_list = QListWidget(tab)
        self._world_list.itemDoubleClicked.connect(self._open_selected_world)
        layout.addWidget(self._world_list)

        open_world_btn = QPushButton("Open Selected World", tab)
        open_world_btn.clicked.connect(self._open_selected_world)
        layout.addWidget(open_world_btn)

        self._tabs.addTab(tab, "Files")

    def _open_server_folder(self) -> None:
        if not self._selected_server_id:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return
        if not _open_path(Path(info.server_dir)):
            QMessageBox.warning(self, "Open Folder", "Could not open server folder")

    def _open_mods_folder(self) -> None:
        if not self._selected_server_id:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return
        mods = Path(info.server_dir) / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        if not _open_path(mods):
            QMessageBox.warning(self, "Open Folder", "Could not open mods folder")

    def _refresh_files(self, info: ServerInfo) -> None:
        self._world_list.clear()
        for world in _iter_world_dirs(Path(info.server_dir)):
            self._world_list.addItem(world.name)

    def _open_selected_world(self, *_args) -> None:
        if not self._selected_server_id:
            return
        item = self._world_list.currentItem()
        if not item:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return

        world_dir = Path(info.server_dir) / item.text()
        if not _open_path(world_dir):
            QMessageBox.warning(self, "Open World", "Could not open selected world folder")

