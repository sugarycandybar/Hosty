"""
Hosty Windows Frontend — PySide6 application.

Main window with sidebar + tabbed content, system theme support,
and full feature parity with the Linux GTK4/Adwaita build.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hosty.shared.backend.playit_config import load_playit_config
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.core.events import set_main_thread_dispatcher
from hosty.shared.utils.constants import ServerStatus

from .theme import ThemeManager
from .utils import _MainThreadInvoker, HAS_PSUTIL
from .mixins import (
    SidebarMixin,
    ConsoleMixin,
    PerformanceMixin,
    PropertiesMixin,
    FilesMixin,
    ConnectMixin,
)


class HostyMainWindow(
    QMainWindow,
    SidebarMixin,
    ConsoleMixin,
    PerformanceMixin,
    PropertiesMixin,
    FilesMixin,
    ConnectMixin,
):
    """Main Hosty application window."""

    def __init__(self, server_manager: ServerManager):
        super().__init__()
        self._server_manager = server_manager
        self._selected_server_id: Optional[str] = None
        self._selected_process = None
        self._status_handler_id = None
        self._output_handler_id = None
        self._process_start_ts: Optional[float] = None
        self._ignore_list_events = False
        self._tps_value = 20.0
        self._psutil_process = None
        self._last_running_server_id = self._server_manager.get_running_server_id()
        self._playit_starting_server_id = None
        self._playit_autostart_paused_server_id: Optional[str] = None

        self.setWindowTitle("Hosty")
        self.resize(1200, 760)
        self.setMinimumSize(700, 500)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, root)
        layout.addWidget(splitter)

        self._build_sidebar(splitter)
        self._build_content(splitter)
        splitter.setSizes([280, 920])

        # Connect server manager signals
        self._manager_added_id = self._server_manager.connect("server-added", self._on_server_added)
        self._manager_removed_id = self._server_manager.connect("server-removed", self._on_server_removed)
        self._manager_changed_id = self._server_manager.connect("server-changed", self._on_server_changed)

        # Stats polling timer
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._on_stats_tick)
        self._stats_timer.start()

        # Playit runtime timer
        self._playit_timer = QTimer(self)
        self._playit_timer.setInterval(1000)
        self._playit_timer.timeout.connect(self._poll_runtime_state)
        self._playit_timer.start()

        self._populate_servers()

    def _build_content(self, splitter: QSplitter) -> None:
        content = QWidget(splitter)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget(content)
        header.setProperty("class", "header-bar")
        header.setFixedHeight(58)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 20, 0) # Less left margin for tabs


        # Navigation Tabs in Header
        self._tabs = QTabBar(header)
        self._tabs.setProperty("class", "header-tabs")
        self._tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tabs.addTab("Console")
        self._tabs.addTab("Connect")
        self._tabs.addTab("Performance")
        self._tabs.addTab("Properties")
        self._tabs.addTab("Files")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        header_layout.addWidget(self._tabs)

        header_layout.addStretch(1)

        self._toggle_btn = QPushButton("Start", content)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setProperty("class", "start")
        self._toggle_btn.setEnabled(False)
        self._toggle_btn.setMinimumWidth(100)
        self._toggle_btn.clicked.connect(self._toggle_server)
        header_layout.addWidget(self._toggle_btn)

        layout.addWidget(header)

        # Content stack
        self._content_stack = QStackedWidget(content)
        layout.addWidget(self._content_stack)

        self._build_console_tab()
        self._build_connect_tab()
        self._build_performance_tab()
        self._build_properties_tab()
        self._build_files_tab()

    def _on_tab_changed(self, index: int) -> None:
        self._content_stack.setCurrentIndex(index)

    def _on_process_output_with_tps(self, process, text: str) -> None:
        """Process output handler that also parses TPS."""
        self._on_process_output(process, text)
        self._parse_tps(text)

    def _attach_process(self, process) -> None:
        """Override to also connect TPS parsing."""
        if self._selected_process == process and process is not None:
            return

        if self._selected_process and self._status_handler_id:
            try:
                self._selected_process.disconnect(self._status_handler_id)
            except Exception:
                pass
        if self._selected_process and self._output_handler_id:
            try:
                self._selected_process.disconnect(self._output_handler_id)
            except Exception:
                pass

        self._selected_process = process
        self._status_handler_id = None
        self._output_handler_id = None
        self._process_start_ts = None
        self._psutil_process = None
        self._tps_value = 20.0

        self._console_output.clear()

        if process:
            self._status_handler_id = process.connect("status-changed", self._on_process_status)
            self._output_handler_id = process.connect("output-received", self._on_process_output_with_tps)
            if process.is_running:
                self._process_start_ts = time.time()
            
            for line in process.log_history:
                self._on_process_output_with_tps(None, line)

    # ===== Playit runtime management (matches Linux window.py) =====

    def _poll_runtime_state(self) -> None:
        running_id = self._server_manager.get_running_server_id()
        prefs = self._server_manager.preferences

        if running_id != self._last_running_server_id:
            previous_id = self._last_running_server_id
            self._last_running_server_id = running_id

            if running_id:
                self._apply_playit_runtime(previous_id, running_id)
            else:
                self._apply_playit_runtime(previous_id, None)

                if previous_id:
                    if prefs.auto_backup_on_stop:
                        self._start_auto_backup(previous_id)
        else:
            self._apply_playit_runtime(None, running_id)

    def _apply_playit_runtime(self, previous_id: Optional[str], running_id: Optional[str]) -> None:
        playit = self._server_manager.playit_manager

        if running_id != self._playit_autostart_paused_server_id and previous_id == self._playit_autostart_paused_server_id:
            self._playit_autostart_paused_server_id = None

        if previous_id and previous_id != running_id and playit.is_running_for(previous_id):
            playit.stop()

        if not running_id:
            self._playit_autostart_paused_server_id = None
            return

        if running_id == self._playit_autostart_paused_server_id:
            return

        info = self._server_manager.get_server(running_id)
        if not info:
            return

        cfg = load_playit_config(info.server_dir)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("auto_start", True):
            return

        if playit.is_running_for(running_id):
            return

        if self._playit_starting_server_id == running_id:
            return

        self._playit_starting_server_id = running_id

        def worker():
            playit.start(
                running_id,
                str(info.server_dir),
                secret=str(cfg.get("secret", "")).strip(),
                auto_install=bool(cfg.get("auto_install", True)),
            )
            self._playit_starting_server_id = None

        threading.Thread(target=worker, daemon=True).start()

    def _start_auto_backup(self, server_id: str) -> None:
        def worker():
            ok, msg = self._server_manager.create_world_backup(server_id, auto=True)
            # No UI toast for now — the backup just happens silently

        threading.Thread(target=worker, daemon=True).start()

    def shutdown(self) -> None:
        """Clean shutdown."""
        self._stats_timer.stop()
        self._playit_timer.stop()


class HostyWindowsApplication:
    """Hosty frontend for Windows, implemented with PySide6."""

    def run(self, argv: list[str]) -> int:
        app = QApplication(argv)
        app.setApplicationName("Hosty")

        # Application Icon
        icon_path = Path(__file__).parents[3] / "packaging" / "linux" / "io.github.sugarycandybar.Hosty.svg"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))

        server_manager = ServerManager()

        # Theme system
        theme = ThemeManager(app, server_manager.preferences)
        app.theme_manager = theme

        # Main thread invoker for backend callbacks
        invoker = _MainThreadInvoker()
        set_main_thread_dispatcher(
            lambda callback, *args, **kwargs: invoker.invoke.emit(callback, args, kwargs)
        )

        window = HostyMainWindow(server_manager)
        theme.register_window(window)
        window.show()

        try:
            return app.exec()
        finally:
            try:
                window.shutdown()
            except Exception:
                pass
            try:
                server_manager.stop_all()
            except Exception:
                pass
            try:
                theme.stop()
            except Exception:
                pass
            set_main_thread_dispatcher(None)
