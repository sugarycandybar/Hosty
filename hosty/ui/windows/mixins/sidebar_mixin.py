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

class SidebarMixin:
    def _build_sidebar(self, splitter: QSplitter) -> None:
        side = QWidget(splitter)
        layout = QVBoxLayout(side)

        controls = QHBoxLayout()
        self._new_btn = QPushButton("New")
        self._rename_btn = QPushButton("Rename")
        self._delete_btn = QPushButton("Delete")
        controls.addWidget(self._new_btn)
        controls.addWidget(self._rename_btn)
        controls.addWidget(self._delete_btn)
        layout.addLayout(controls)

        self._server_list = QListWidget(side)
        layout.addWidget(self._server_list)

        self._new_btn.clicked.connect(self._show_create_dialog)
        self._rename_btn.clicked.connect(self._rename_selected)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._server_list.currentItemChanged.connect(self._on_server_selected)

    def _populate_servers(self) -> None:
        self._ignore_list_events = True
        self._server_list.clear()
        for info in self._server_manager.servers:
            self._add_or_update_item(info)
        self._ignore_list_events = False

        if self._server_list.count() > 0:
            self._server_list.setCurrentRow(0)
        else:
            self._clear_selection_state()

    def _add_or_update_item(self, info: ServerInfo) -> None:
        process = self._server_manager.get_process(info.id)
        status = process.status if process else ServerStatus.STOPPED
        label = f"{_status_prefix(status)} {info.name}  ({info.mc_version})"

        for i in range(self._server_list.count()):
            item = self._server_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == info.id:
                item.setText(label)
                return

        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, info.id)
        self._server_list.addItem(item)

    def _refresh_server_rows_status(self) -> None:
        for info in self._server_manager.servers:
            self._add_or_update_item(info)

    def _on_server_added(self, _manager, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return
        self._add_or_update_item(info)
        self._select_server(server_id)

    def _on_server_removed(self, _manager, server_id: str) -> None:
        for i in range(self._server_list.count()):
            item = self._server_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == server_id:
                self._server_list.takeItem(i)
                break

        if self._server_list.count() == 0:
            self._clear_selection_state()

    def _on_server_changed(self, _manager, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return
        self._add_or_update_item(info)
        if self._selected_server_id == server_id:
            self._load_server(info)

    def _on_server_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if self._ignore_list_events:
            return
        if not current:
            self._clear_selection_state()
            return

        server_id = current.data(Qt.ItemDataRole.UserRole)
        info = self._server_manager.get_server(server_id)
        if not info:
            self._clear_selection_state()
            return

        self._selected_server_id = server_id
        self._load_server(info)

    def _select_server(self, server_id: str) -> None:
        for i in range(self._server_list.count()):
            item = self._server_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == server_id:
                self._server_list.setCurrentItem(item)
                return

    def _load_server(self, info: ServerInfo) -> None:
        self._title_label.setText(f"{info.name} · {info.mc_version}")
        self._toggle_btn.setEnabled(True)

        process = self._server_manager.get_process(info.id)
        self._attach_process(process)
        self._load_properties(info)
        self._refresh_files(info)
        self._refresh_toggle_button()
        self._refresh_performance()

    def _clear_selection_state(self) -> None:
        self._selected_server_id = None
        self._title_label.setText("Select a server")
        self._toggle_btn.setEnabled(False)
        self._properties_editor.clear()
        self._world_list.clear()
        self._attach_process(None)
        self._refresh_performance()

    def _attach_process(self, process) -> None:
        if self._selected_process and self._status_handler_id:
            self._selected_process.disconnect(self._status_handler_id)
        if self._selected_process and self._output_handler_id:
            self._selected_process.disconnect(self._output_handler_id)

        self._selected_process = process
        self._status_handler_id = None
        self._output_handler_id = None
        self._process_start_ts = None

        self._console_output.clear()

        if process:
            self._status_handler_id = process.connect("status-changed", self._on_process_status)
            self._output_handler_id = process.connect("output-received", self._on_process_output)
            if process.is_running:
                self._process_start_ts = time.time()

    def _on_process_status(self, _process, status: str) -> None:
        if status in (ServerStatus.STARTING, ServerStatus.RUNNING):
            if self._process_start_ts is None:
                self._process_start_ts = time.time()
        elif status == ServerStatus.STOPPED:
            self._process_start_ts = None

        self._refresh_toggle_button()
        self._refresh_performance()
        self._refresh_server_rows_status()

    def _refresh_toggle_button(self) -> None:
        if not self._selected_process:
            self._toggle_btn.setEnabled(False)
            self._toggle_btn.setText("Start")
            return

        if self._selected_process.is_running:
            self._toggle_btn.setEnabled(True)
            self._toggle_btn.setText("Stop")
            return

        blocked = self._server_manager.is_any_server_running()
        self._toggle_btn.setEnabled(not blocked)
        self._toggle_btn.setText("Start")

    def _toggle_server(self) -> None:
        if not self._selected_process:
            return

        if self._selected_process.is_running:
            self._selected_process.stop()
            return

        if self._server_manager.is_any_server_running() and not self._selected_process.is_running:
            QMessageBox.warning(
                self,
                "Cannot Start Server",
                "Another server is already running. Stop it first.",
            )
            return

        ok = self._selected_process.start()
        if not ok:
            QMessageBox.warning(self, "Start Failed", "Unable to start server process.")

    def _show_create_dialog(self) -> None:
        dialog = CreateServerDialog(self._server_manager, self)
        dialog.server_created.connect(self._select_server)
        dialog.exec()

    def _rename_selected(self) -> None:
        if not self._selected_server_id:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return

        new_name, ok = QInputDialog.getText(self, "Rename Server", "New server name", text=info.name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        self._server_manager.rename_server(info.id, new_name)

    def _delete_selected(self) -> None:
        if not self._selected_server_id:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return

        result = QMessageBox.question(
            self,
            "Delete Server",
            f"Delete '{info.name}' and all its files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        self._server_manager.delete_server(info.id, delete_files=True)

