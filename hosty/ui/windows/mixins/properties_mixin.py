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

class PropertiesMixin:
    def _build_properties_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)

        self._properties_editor = QTextEdit(tab)
        self._properties_editor.setAcceptRichText(False)
        layout.addWidget(self._properties_editor)

        save_btn = QPushButton("Save Properties", tab)
        save_btn.clicked.connect(self._save_properties)
        layout.addWidget(save_btn)

        self._tabs.addTab(tab, "Properties")

    def _load_properties(self, info: ServerInfo) -> None:
        config = self._server_manager.get_config(info.id)
        if not config:
            self._properties_editor.clear()
            return

        props = dict(DEFAULT_SERVER_PROPERTIES)
        props.update(config.load())
        lines = [f"{k}={v}" for k, v in props.items()]
        self._properties_editor.setPlainText("\n".join(lines) + "\n")

    def _save_properties(self) -> None:
        if not self._selected_server_id:
            return
        config = self._server_manager.get_config(self._selected_server_id)
        if not config:
            return

        raw = self._properties_editor.toPlainText().splitlines()
        for line in raw:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config.set_value(key.strip(), value.strip())
        config.save()

        self._server_manager.emit_on_main_thread("server-changed", self._selected_server_id)
        QMessageBox.information(self, "Properties", "server.properties saved")

