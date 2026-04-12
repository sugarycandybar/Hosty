"""
Worlds page for the Files tab.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hosty.backend.server_manager import ServerInfo, ServerManager
from ..components import SmoothScrollArea
from ..utils import _iter_world_dirs, _open_path


class WorldsPage(QWidget):
    def __init__(self, server_manager: ServerManager, back_callback):
        super().__init__()
        self._server_manager = server_manager
        self._server_info = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setProperty("class", "header-bar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)

        back_btn = QPushButton("← Back")
        back_btn.setProperty("class", "flat")
        back_btn.clicked.connect(back_callback)
        header_layout.addWidget(back_btn)

        title = QLabel("Worlds")
        title.setProperty("class", "title")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        layout.addWidget(header)

        # Content
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(12)

        self._scroll = SmoothScrollArea()
        self._world_list = QListWidget()
        self._world_list.itemDoubleClicked.connect(self._open_selected_world)
        content_layout.addWidget(self._world_list)

        open_btn = QPushButton("Open Selected World Folder")
        open_btn.setProperty("class", "accent")
        open_btn.clicked.connect(self._open_selected_world)
        content_layout.addWidget(open_btn)

        layout.addWidget(content, 1)

    def load_server(self, info: ServerInfo) -> None:
        self._server_info = info
        self._world_list.clear()
        
        if not info.server_dir.exists():
            return
            
        for world in _iter_world_dirs(Path(info.server_dir)):
            item = QListWidgetItem(f"🌍  {world.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(world))
            self._world_list.addItem(item)

    def _open_selected_world(self) -> None:
        item = self._world_list.currentItem()
        if not item:
            QMessageBox.information(self, "Worlds", "Select a world first.")
            return
            
        world_path = item.data(Qt.ItemDataRole.UserRole)
        if world_path and not _open_path(Path(world_path)):
            QMessageBox.warning(self, "Open World", "Could not open selected world folder")
