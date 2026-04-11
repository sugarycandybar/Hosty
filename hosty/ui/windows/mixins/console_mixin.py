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

class ConsoleMixin:
    def _build_console_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)

        self._console_output = QPlainTextEdit(tab)
        self._console_output.setReadOnly(True)
        layout.addWidget(self._console_output)

        row = QHBoxLayout()

        self._command_input = QLineEdit(tab)
        self._command_input.setPlaceholderText("Type a command...")
        self._command_input.returnPressed.connect(self._send_command)
        row.addWidget(self._command_input)

        clear_btn = QPushButton("Clear", tab)
        clear_btn.clicked.connect(self._console_output.clear)
        row.addWidget(clear_btn)

        send_btn = QPushButton("Send", tab)
        send_btn.clicked.connect(self._send_command)
        row.addWidget(send_btn)

        layout.addLayout(row)
        self._tabs.addTab(tab, "Console")

    def _on_process_output(self, _process, text: str) -> None:
        self._console_output.appendPlainText(text.rstrip("\n"))
        scrollbar = self._console_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _send_command(self) -> None:
        text = self._command_input.text().strip()
        if not text:
            return

        if self._selected_process:
            self._console_output.appendPlainText(f"> {text}")
            self._selected_process.send_command(text)
        else:
            self._console_output.appendPlainText("[Hosty] No process connected")
        self._command_input.clear()

