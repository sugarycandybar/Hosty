"""
Files mixin — server folders, worlds, and backup creation.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hosty.backend.server_manager import ServerInfo, ServerManager

from ..utils import _iter_world_dirs, _open_path


class FilesMixin:
    """Mixin providing file management: folders, worlds, backups."""

    def _build_files_tab(self) -> None:
        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Quick actions
        actions_group = QGroupBox("Quick Actions")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setSpacing(8)

        btn_row = QHBoxLayout()
        open_server = QPushButton("📂  Open Server Folder")
        open_server.clicked.connect(self._open_server_folder)
        btn_row.addWidget(open_server)

        open_mods = QPushButton("🧩  Open Mods Folder")
        open_mods.clicked.connect(self._open_mods_folder)
        btn_row.addWidget(open_mods)
        actions_layout.addLayout(btn_row)

        layout.addWidget(actions_group)

        # Worlds
        worlds_group = QGroupBox("Worlds")
        worlds_layout = QVBoxLayout(worlds_group)
        worlds_layout.setSpacing(8)

        self._world_list = QListWidget()
        self._world_list.setMaximumHeight(180)
        self._world_list.itemDoubleClicked.connect(self._open_selected_world)
        worlds_layout.addWidget(self._world_list)

        open_world_btn = QPushButton("Open Selected World")
        open_world_btn.clicked.connect(self._open_selected_world)
        worlds_layout.addWidget(open_world_btn)

        layout.addWidget(worlds_group)

        # Backups
        backups_group = QGroupBox("Backups")
        backups_layout = QVBoxLayout(backups_group)
        backups_layout.setSpacing(8)

        backup_info = QLabel("Create a zip backup of all world folders")
        backup_info.setProperty("class", "dim")
        backups_layout.addWidget(backup_info)

        self._backup_btn = QPushButton("💾  Create World Backup")
        self._backup_btn.clicked.connect(self._create_backup)
        backups_layout.addWidget(self._backup_btn)

        self._backup_status = QLabel("")
        self._backup_status.setProperty("class", "dim")
        self._backup_status.setVisible(False)
        backups_layout.addWidget(self._backup_status)

        layout.addWidget(backups_group)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
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
            item = QListWidgetItem(f"🌍  {world.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(world))
            self._world_list.addItem(item)

    def _open_selected_world(self, *_args) -> None:
        if not self._selected_server_id:
            return
        item = self._world_list.currentItem()
        if not item:
            return
        world_path = item.data(Qt.ItemDataRole.UserRole)
        if world_path and not _open_path(Path(world_path)):
            QMessageBox.warning(self, "Open World", "Could not open selected world folder")

    def _create_backup(self) -> None:
        if not self._selected_server_id:
            return

        self._backup_btn.setEnabled(False)
        self._backup_btn.setText("Creating backup…")
        self._backup_status.setVisible(False)

        server_id = self._selected_server_id

        def worker():
            ok, msg = self._server_manager.create_world_backup(server_id)

            def ui_done():
                self._backup_btn.setEnabled(True)
                self._backup_btn.setText("💾  Create World Backup")
                self._backup_status.setVisible(True)
                if ok:
                    self._backup_status.setText(f"✅ Backup created: {msg}")
                else:
                    self._backup_status.setText(f"⚠️ Backup failed: {msg}")

            # Schedule on main thread
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, ui_done)

        threading.Thread(target=worker, daemon=True).start()
