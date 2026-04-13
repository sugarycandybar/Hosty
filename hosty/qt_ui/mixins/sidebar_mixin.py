"""
Sidebar mixin — server list with status dots, context menus, and management.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QColor, QPainter, QPixmap, QIcon, QPainterPath
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.utils.constants import ServerStatus

from ..utils import _status_color_hex, _status_prefix
from ..dialogs.create_server import CreateServerDialog


class ServerListItemWidget(QWidget):
    """Custom widget for server list items with icon, text, and status dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 4, 16, 4)
        self.layout.setSpacing(12)
        # Alignment is best set on addWidget calls

        # Icon
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(50, 50)
        self.icon_label.setScaledContents(True)
        self.layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Text container
        self.text_container = QWidget()
        self.text_layout = QVBoxLayout(self.text_container)
        self.text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_layout.setSpacing(2)

        self.name_label = QLabel()
        self.name_label.setObjectName("server_name")
        self.text_layout.addWidget(self.name_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("server_subtitle")
        self.text_layout.addWidget(self.subtitle_label)

        self.layout.addWidget(self.text_container, 1, Qt.AlignmentFlag.AlignVCenter)

        # Status dot
        self.status_dot = QWidget()
        self.status_dot.setFixedSize(12, 12)
        self.status_dot.setStyleSheet("border-radius: 6px; background-color: #5e6182;")
        self.layout.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)

    def update_info(self, name: str, subtitle: str, icon: QPixmap, status_color: str):
        self.name_label.setText(name)
        self.subtitle_label.setText(subtitle)
        self.icon_label.setPixmap(icon)
        self.status_dot.setStyleSheet(f"border-radius: 6px; background-color: {status_color};")


class SidebarMixin:
    """Mixin providing a modern sidebar with server list and management."""

    def _build_sidebar(self, splitter: QSplitter) -> None:
        side = QWidget(splitter)
        side.setProperty("class", "sidebar")
        side.setMinimumWidth(240)
        side.setMaximumWidth(360)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget(side)
        header.setProperty("class", "header-bar")
        header.setFixedHeight(58)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(30, 30)
        add_btn.setToolTip("Create new server")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setProperty("class", "flat")
        add_btn.setStyleSheet("font-size: 18px; font-weight: 700;")
        add_btn.clicked.connect(self._show_create_dialog)
        header_layout.addWidget(add_btn)

        title_label = QLabel("Servers")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet(
            "font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;"
            "font-weight: 700; font-size: 15px; letter-spacing: -0.3px;"
        )
        header_layout.addWidget(title_label, 1)

        menu_btn = QPushButton("⋯")
        menu_btn.setFixedSize(30, 30)
        menu_btn.setToolTip("Menu")
        menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_btn.setProperty("class", "flat")
        menu_btn.setStyleSheet("font-size: 16px; font-weight: 700;")
        menu_btn.clicked.connect(self._show_app_menu)
        header_layout.addWidget(menu_btn)

        layout.addWidget(header)

        # Server list
        self._server_list = QListWidget(side)
        self._server_list.setIconSize(QSize(64, 64))
        self._server_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._server_list.customContextMenuRequested.connect(self._on_context_menu)
        self._server_list.currentItemChanged.connect(self._on_server_selected)
        self._server_list.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._server_list)

    def _show_app_menu(self) -> None:
        """Show the application hamburger menu."""
        menu = QMenu(self)
        prefs_action = menu.addAction("Preferences")
        prefs_action.triggered.connect(self._show_preferences)
        about_action = menu.addAction("About Hosty")
        about_action.triggered.connect(self._show_about)
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _show_preferences(self) -> None:
        from ..dialogs.preferences import PreferencesDialog
        dlg = PreferencesDialog(self._server_manager.preferences, self)
        dlg.exec()

    def _show_about(self) -> None:
        from ..dialogs.about import AboutDialog
        dlg = AboutDialog(self)
        dlg.exec()

    def _on_context_menu(self, pos) -> None:
        """Right-click context menu on a server row."""
        item = self._server_list.itemAt(pos)
        if not item:
            return

        server_id = item.data(Qt.ItemDataRole.UserRole)
        info = self._server_manager.get_server(server_id)
        if not info:
            return

        self._server_list.setCurrentItem(item)

        menu = QMenu(self)

        rename_action = menu.addAction("Rename…")
        rename_action.triggered.connect(lambda: self._rename_server(server_id))

        icon_action = menu.addAction("Change Icon…")
        icon_action.triggered.connect(lambda: self._change_icon(server_id))

        menu.addSeparator()

        delete_action = menu.addAction("Delete")
        delete_action.triggered.connect(lambda: self._delete_server(server_id))

        menu.exec(self._server_list.mapToGlobal(pos))

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

    def _make_server_icon_pixmap(self, info: ServerInfo) -> QPixmap:
        """Create a rounded server icon pixmap."""
        size = 120  # Draw at higher res then scale down for sharpness
        display_size = 64
        
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        # Base icon search
        icon_path = info.icon_path
        if not icon_path or not Path(icon_path).exists():
            default_icon_local = Path(info.server_dir) / "server-icon.png"
            if default_icon_local.exists():
                icon_path = str(default_icon_local)
            else:
                # Use global default
                global_default = Path("images/default_server_icon.png")
                if global_default.exists():
                    icon_path = str(global_default)
                
        if icon_path and Path(icon_path).exists():
            base = QPixmap(icon_path).scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
        else:
            # Absolute fallback
            base = QPixmap(size, size)
            base.fill(Qt.GlobalColor.transparent)
            bp = QPainter(base)
            bp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            bp.setBrush(QColor("#3d2e23"))
            bp.setPen(Qt.PenStyle.NoPen)
            bp.drawRoundedRect(0, 0, size, size, 24, 24)
            bp.end()

        # Draw rounded
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, 24, 24)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, base)
        painter.end()

        return pixmap.scaled(
            display_size, display_size, 
            Qt.AspectRatioMode.IgnoreAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )

    def _add_or_update_item(self, info: ServerInfo) -> None:
        process = self._server_manager.get_process(info.id)
        status = process.status if process else ServerStatus.STOPPED

        # Build display text
        subtitle = info.mc_version
        if process and process.is_running:
            subtitle = f"{info.mc_version} · {process.player_count}/{process.max_players}"
        
        status_color = _status_color_hex(status)
        pixmap = self._make_server_icon_pixmap(info)

        for i in range(self._server_list.count()):
            item = self._server_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == info.id:
                widget = self._server_list.itemWidget(item)
                if isinstance(widget, ServerListItemWidget):
                    widget.update_info(info.name, subtitle, pixmap, status_color)
                return

        item = QListWidgetItem(self._server_list)
        item.setData(Qt.ItemDataRole.UserRole, info.id)
        item.setSizeHint(QSize(0, 84))
        
        widget = ServerListItemWidget()
        widget.update_info(info.name, subtitle, pixmap, status_color)
        
        self._server_list.addItem(item)
        self._server_list.setItemWidget(item, widget)

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
        self.setWindowTitle(f"Hosty — {info.name} · {info.mc_version}")
        self._toggle_btn.setEnabled(True)

        process = self._server_manager.get_process(info.id)
        self._attach_process(process)
        self._load_properties(info)
        self._refresh_files(info)
        self._refresh_connect(info)
        self._refresh_toggle_button()
        self._refresh_performance()

    def _clear_selection_state(self) -> None:
        self._selected_server_id = None
        self.setWindowTitle("Hosty")
        self._toggle_btn.setEnabled(False)
        self._toggle_btn.setText("Start")
        self._toggle_btn.setProperty("class", "start")
        self._toggle_btn.style().unpolish(self._toggle_btn)
        self._toggle_btn.style().polish(self._toggle_btn)
        self._console_output.clear()
        self._attach_process(None)
        self._refresh_performance()

    def _attach_process(self, process) -> None:
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
            self._toggle_btn.setProperty("class", "start")
            self._toggle_btn.style().unpolish(self._toggle_btn)
            self._toggle_btn.style().polish(self._toggle_btn)
            return

        status = self._selected_process.status

        if status == ServerStatus.STARTING:
            self._toggle_btn.setEnabled(False)
            self._toggle_btn.setText("Starting…")
            self._toggle_btn.setProperty("class", "")
            self._toggle_btn.setToolTip("Wait for the server to finish starting")
        elif status == ServerStatus.RUNNING:
            self._toggle_btn.setEnabled(True)
            self._toggle_btn.setText("Stop")
            self._toggle_btn.setProperty("class", "stop")
            self._toggle_btn.setToolTip("")
        else:
            blocked = self._server_manager.is_any_server_running()
            mods_busy = False
            if self._selected_server_id:
                mods_busy = self._server_manager.is_mod_operation_active(self._selected_server_id)

            self._toggle_btn.setEnabled(not blocked and not mods_busy)
            self._toggle_btn.setText("Start")
            self._toggle_btn.setProperty("class", "start")
            if mods_busy:
                self._toggle_btn.setToolTip("Mods are currently installing/updating")
            elif blocked:
                self._toggle_btn.setToolTip("Another server is already running")
            else:
                self._toggle_btn.setToolTip("")

        self._toggle_btn.style().unpolish(self._toggle_btn)
        self._toggle_btn.style().polish(self._toggle_btn)

    def _toggle_server(self) -> None:
        if not self._selected_process:
            return

        if self._selected_process.status == ServerStatus.STARTING:
            return

        if self._selected_process.is_running:
            self._selected_process.stop()
            return

        if self._selected_server_id and self._server_manager.is_mod_operation_active(self._selected_server_id):
            QMessageBox.warning(
                self,
                "Cannot Start Server",
                "Mods are currently being installed or updated. Please wait.",
            )
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

    def _rename_server(self, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Server", "New server name:", text=info.name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        self._server_manager.rename_server(info.id, new_name)

    def _change_icon(self, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Server Icon",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return

        try:
            from hosty.shared.utils.image_utils import convert_to_png
            icon_output = Path(info.server_dir) / "icon.png"
            convert_to_png(path, str(icon_output), size=128)
            self._server_manager.set_server_icon(server_id, str(icon_output))
        except Exception as e:
            QMessageBox.warning(self, "Icon Error", f"Could not set icon: {e}")

    def _delete_server(self, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return

        result = QMessageBox.question(
            self,
            "Delete Server",
            f'Are you sure you want to delete "{info.name}"?\n\nAll server files will be permanently deleted.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        self._server_manager.delete_server(info.id, delete_files=True)
