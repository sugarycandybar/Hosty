"""
Backups page for the Files tab.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

from ..components import SmoothScrollArea
from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.core.events import dispatch_on_main_thread


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_mtime(mtime: float) -> str:
    dt = datetime.fromtimestamp(mtime)
    return dt.strftime("%b %d, %Y %I:%M %p")


class BackupsPage(QWidget):
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
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(back_callback)
        header_layout.addWidget(back_btn)

        title = QLabel("Backups")
        title.setProperty("class", "title")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        self._create_btn = QPushButton("Create Backup")
        self._create_btn.setProperty("class", "accent")
        self._create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._create_btn.clicked.connect(self._create_backup)
        header_layout.addWidget(self._create_btn)

        layout.addWidget(header)

        # Status label (replaces modal popups)
        self._status_lbl = QLabel("")
        self._status_lbl.setProperty("class", "dim")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setContentsMargins(24, 4, 24, 4)
        layout.addWidget(self._status_lbl)

        # Backup List Content
        self._scroll = SmoothScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(24, 24, 24, 24)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, 1)

    def load_server(self, info: ServerInfo) -> None:
        self._server_info = info
        self._create_btn.setText("Create Backup")
        self._create_btn.setEnabled(True)
        self._status_lbl.setText("")
        self._refresh()

    def _refresh(self) -> None:
        # Clear
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._server_info or not self._server_info.server_dir.exists():
            return

        bdir = self._server_info.server_dir / "backups"
        if not bdir.exists():
            bdir.mkdir(parents=True, exist_ok=True)

        zips = sorted(bdir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zips:
            lbl = QLabel("No backups yet. Create one to save a snapshot of all your worlds.")
            lbl.setProperty("class", "dim")
            self._content_layout.addWidget(lbl)
            return

        for zp in zips:
            self._content_layout.addWidget(self._build_row(zp))

    def _build_row(self, zip_path: Path) -> QWidget:
        card = QWidget()
        card.setProperty("class", "card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)

        st = zip_path.stat()
        
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel(zip_path.name)
        title.setProperty("class", "title")
        text_col.addWidget(title)

        sub = QLabel(f"{_format_size(st.st_size)} · {_format_mtime(st.st_mtime)}")
        sub.setProperty("class", "dim")
        text_col.addWidget(sub)

        layout.addLayout(text_col, 1)

        restore_btn = QPushButton("Restore")
        restore_btn.setProperty("class", "flat")
        restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        restore_btn.clicked.connect(lambda *_args, p=zip_path: self._restore_backup(p))
        layout.addWidget(restore_btn)

        del_btn = QPushButton("Delete")
        del_btn.setProperty("class", "destructive")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda *_args, p=zip_path: self._delete_backup(p))
        layout.addWidget(del_btn)

        return card

    def _create_backup(self) -> None:
        if not self._server_info:
            return

        self._create_btn.setEnabled(False)
        self._create_btn.setText("Creating...")
        self._status_lbl.setText("Creating backup…")

        server_id = self._server_info.id

        def worker():
            ok, msg = self._server_manager.create_world_backup(server_id)

            def ui_done():
                self._create_btn.setEnabled(True)
                self._create_btn.setText("Create Backup")
                if ok:
                    self._status_lbl.setText(f"✓ Backup created: {msg}")
                else:
                    self._status_lbl.setText(f"✗ Failed: {msg}")
                self._refresh()

            dispatch_on_main_thread(ui_done)

        threading.Thread(target=worker, daemon=True).start()

    def _restore_backup(self, zip_path: Path) -> None:
        if not self._server_info:
            return

        proc = self._server_manager.get_process(self._server_info.id)
        if proc and proc.is_running:
            self._status_lbl.setText("⚠ Stop the server before restoring a backup.")
            return

        reply = QMessageBox.question(
            self,
            "Restore Backup",
            f"Are you sure you want to restore '{zip_path.name}'?\n\nThis will overwrite existing world files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._create_btn.setEnabled(False)
        self._create_btn.setText("Restoring...")
        self._status_lbl.setText("Restoring backup…")

        server_id = self._server_info.id

        def worker():
            ok, msg = self._server_manager.restore_world_backup(server_id, zip_path)

            def ui_done():
                self._create_btn.setEnabled(True)
                self._create_btn.setText("Create Backup")
                if ok:
                    self._status_lbl.setText("✓ Backup restored successfully.")
                else:
                    self._status_lbl.setText(f"✗ Restore failed: {msg}")
                self._refresh()

            dispatch_on_main_thread(ui_done)

        threading.Thread(target=worker, daemon=True).start()

    def _delete_backup(self, zip_path: Path) -> None:
        reply = QMessageBox.question(
            self,
            "Delete Backup",
            f"Are you sure you want to permanently delete '{zip_path.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "Delete Failed", f"Could not delete backup: {e}")
            self._refresh()
