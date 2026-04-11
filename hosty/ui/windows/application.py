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



from .utils import *
from .dialogs import CreateServerDialog
from .mixins import SidebarMixin, ConsoleMixin, PerformanceMixin, PropertiesMixin, FilesMixin

class HostyMainWindow(QMainWindow, SidebarMixin, ConsoleMixin, PerformanceMixin, PropertiesMixin, FilesMixin):
    def __init__(self, server_manager: ServerManager):
        super().__init__()
        self._server_manager = server_manager
        self._selected_server_id: Optional[str] = None
        self._selected_process = None
        self._status_handler_id = None
        self._output_handler_id = None
        self._process_start_ts: Optional[float] = None
        self._ignore_list_events = False

        self.setWindowTitle("Hosty")
        self.resize(1200, 760)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal, root)
        layout.addWidget(splitter)

        self._build_sidebar(splitter)
        self._build_content(splitter)
        splitter.setSizes([330, 870])

        self._manager_added_id = self._server_manager.connect("server-added", self._on_server_added)
        self._manager_removed_id = self._server_manager.connect("server-removed", self._on_server_removed)
        self._manager_changed_id = self._server_manager.connect("server-changed", self._on_server_changed)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._on_stats_tick)
        self._stats_timer.start()

        self._populate_servers()

    def _build_content(self, splitter: QSplitter) -> None:
        content = QWidget(splitter)
        layout = QVBoxLayout(content)

        header = QHBoxLayout()
        self._title_label = QLabel("Select a server", content)
        self._title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(self._title_label)
        header.addStretch(1)
        self._toggle_btn = QPushButton("Start", content)
        self._toggle_btn.setEnabled(False)
        self._toggle_btn.clicked.connect(self._toggle_server)
        header.addWidget(self._toggle_btn)
        layout.addLayout(header)

        self._tabs = QTabWidget(content)
        layout.addWidget(self._tabs)

        self._build_console_tab()
        self._build_performance_tab()
        self._build_properties_tab()
        self._build_files_tab()


class HostyWindowsApplication:
    """Hosty frontend for Windows, implemented with PySide6."""

    def run(self, argv: list[str]) -> int:
        app = QApplication(argv)
        app.setApplicationName("Hosty")
        app.setStyleSheet(
            "QMainWindow { background: #171a21; }"
            "QWidget { color: #e8ecf1; font-size: 13px; }"
            "QPlainTextEdit, QTextEdit, QListWidget { background: #11141a; border: 1px solid #2c3340; border-radius: 6px; }"
            "QPushButton { background: #2b6cb0; border: none; border-radius: 6px; padding: 6px 10px; }"
            "QPushButton:disabled { background: #3a4354; color: #9aa6bd; }"
            "QTabWidget::pane { border: 1px solid #2c3340; border-radius: 6px; }"
            "QTabBar::tab { background: #1d2230; padding: 6px 10px; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #2b6cb0; }"
        )

        invoker = _MainThreadInvoker()
        set_main_thread_dispatcher(
            lambda callback, *args, **kwargs: invoker.invoke.emit(callback, args, kwargs)
        )

        server_manager = ServerManager()
        window = HostyMainWindow(server_manager)
        window.show()

        try:
            return app.exec()
        finally:
            try:
                server_manager.stop_all()
            except Exception:
                pass
            set_main_thread_dispatcher(None)
