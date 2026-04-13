"""
Installed Mods page for the Files tab.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from ..components import SmoothScrollArea

from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.core.events import dispatch_on_main_thread
from ..utils import _open_path


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


class ModsPage(QWidget):
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

        title = QLabel("Installed Mods")
        title.setProperty("class", "title")
        header_layout.addWidget(title)
        
        header_layout.addStretch()

        open_folder = QPushButton("Open Folder")
        open_folder.setProperty("class", "accent")
        open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder.clicked.connect(self._open_mods_folder)
        header_layout.addWidget(open_folder)

        layout.addWidget(header)

        # Status label (replaces popups)
        self._status_lbl = QLabel("")
        self._status_lbl.setProperty("class", "dim")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setContentsMargins(24, 4, 24, 4)
        layout.addWidget(self._status_lbl)

        # Mod List Content
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
        self._refresh()

    def _refresh(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._server_info or not self._server_info.server_dir.exists():
            return

        mods_dir = self._server_info.server_dir / "mods"
        if not mods_dir.exists() or not mods_dir.is_dir():
            lbl = QLabel("No mods installed yet.")
            lbl.setProperty("class", "dim")
            self._content_layout.addWidget(lbl)
            return

        jars = sorted(mods_dir.glob("*.jar"), key=lambda p: p.name.lower())
        if not jars:
            lbl = QLabel("No mod .jar files found.")
            lbl.setProperty("class", "dim")
            self._content_layout.addWidget(lbl)
            return

        for jar in jars:
            self._content_layout.addWidget(self._build_row(jar))

    def _build_row(self, jar: Path) -> QWidget:
        card = QWidget()
        card.setProperty("class", "card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)

        st = jar.stat()
        
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel(jar.name)
        title.setProperty("class", "title")
        text_col.addWidget(title)

        sub = QLabel(_format_size(st.st_size))
        sub.setProperty("class", "dim")
        text_col.addWidget(sub)

        layout.addLayout(text_col, 1)

        del_btn = QPushButton("Delete")
        del_btn.setProperty("class", "destructive")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda *_args, p=jar: self._delete_mod(p))
        layout.addWidget(del_btn)

        return card

    def _open_mods_folder(self) -> None:
        if not self._server_info:
            return
        mods = Path(self._server_info.server_dir) / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        if not _open_path(mods):
            self._status_lbl.setText("⚠ Could not open mods folder")

    def _delete_mod(self, jar: Path) -> None:
        if self._server_info:
            proc = self._server_manager.get_process(self._server_info.id)
            if proc and proc.is_running:
                self._status_lbl.setText("⚠ Stop the server before deleting mods.")
                return

        reply = QMessageBox.question(
            self,
            "Delete Mod",
            f"Delete '{jar.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                jar.unlink(missing_ok=True)
                self._status_lbl.setText(f"✓ Deleted {jar.name}")
            except Exception as e:
                self._status_lbl.setText(f"✗ Could not delete: {e}")
            self._refresh()
