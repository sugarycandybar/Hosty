"""
Files mixin — entry point for Worlds, Backups, Mods, and Modrinth.
Uses QStackedWidget for navigation.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from ..utils import _open_path

# Import our subpages (we will create these next)
from ..pages.backups_page import BackupsPage
from ..pages.worlds_page import WorldsPage
from ..pages.mods_page import ModsPage
from ..pages.modrinth_page import ModrinthPage


class FilesMixin:
    """Mixin for the files tab using a navigation stack."""

    def _build_files_tab(self) -> None:
        self._files_stack = QStackedWidget()
        
        # Main Index Page
        self._files_index = QWidget()
        index_layout = QVBoxLayout(self._files_index)
        index_layout.setContentsMargins(24, 24, 24, 24)
        index_layout.setSpacing(12)

        lbl = QLabel("Server Files & Mods")
        lbl.setProperty("class", "header")
        index_layout.addWidget(lbl)

        # Build category rows
        index_layout.addWidget(self._build_nav_row("Worlds", "public", "Manage world folders", lambda: self._navigate_to(1)))
        index_layout.addWidget(self._build_nav_row("Backups", "save", "Create and restore backups", lambda: self._navigate_to(2)))
        index_layout.addWidget(self._build_nav_row("Installed Mods", "extension", "View and update installed mods", lambda: self._navigate_to(3)))
        index_layout.addWidget(self._build_nav_row("Modrinth", "search", "Discover and install new mods, modpacks, and plugins", lambda: self._navigate_to(4)))
        index_layout.addWidget(self._build_nav_row("Check for Mod Updates", "refresh", "Scan installed mods for newer versions", lambda: self._on_check_mod_updates()))

        # Quick action buttons
        index_layout.addSpacing(20)
        quick_lbl = QLabel("Quick Actions")
        quick_lbl.setProperty("class", "title")
        index_layout.addWidget(quick_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        
        from ..theme import get_material_icon, get_colors, is_system_dark
        icon_color = get_colors(is_system_dark()).get("text_secondary", "#C4B5A3")

        open_srv = QPushButton(" Open Server Folder")
        open_srv.setIcon(get_material_icon("folder_open", icon_color))
        open_srv.setProperty("class", "flat")
        open_srv.setCursor(Qt.CursorShape.PointingHandCursor)
        open_srv.clicked.connect(self._open_server_folder)
        btn_row.addWidget(open_srv)
        
        open_mods = QPushButton(" Open Mods Folder")
        open_mods.setIcon(get_material_icon("folder_open", icon_color))
        open_mods.setProperty("class", "flat")
        open_mods.setCursor(Qt.CursorShape.PointingHandCursor)
        open_mods.clicked.connect(self._open_mods_folder)
        btn_row.addWidget(open_mods)
        
        btn_row.addStretch()
        index_layout.addLayout(btn_row)

        index_layout.addStretch()
        self._files_stack.addWidget(self._files_index) # Index 0

        # Subpages
        self._backups_page = BackupsPage(self._server_manager, self._navigate_home)
        self._worlds_page = WorldsPage(self._server_manager, self._navigate_home)
        self._mods_page = ModsPage(self._server_manager, self._navigate_home)
        self._modrinth_page = ModrinthPage(self._server_manager, self._navigate_home)
        
        self._files_stack.addWidget(self._worlds_page)   # Index 1
        self._files_stack.addWidget(self._backups_page)  # Index 2
        self._files_stack.addWidget(self._mods_page)     # Index 3
        self._files_stack.addWidget(self._modrinth_page) # Index 4

        self._content_stack.addWidget(self._files_stack)

    def _build_nav_row(self, title: str, icon_name: str, subtitle: str, callback) -> QWidget:
        card = QWidget()
        card.setProperty("class", "card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        
        from ..theme import get_material_icon, get_colors, is_system_dark
        icon_color = get_colors(is_system_dark()).get("text_secondary", "#C4B5A3")

        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_material_icon(icon_name, icon_color, 28).pixmap(28, 28))
        layout.addWidget(icon_lbl)
        
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        t_lbl = QLabel(title)
        t_lbl.setProperty("class", "title")
        text_col.addWidget(t_lbl)
        
        s_lbl = QLabel(subtitle)
        s_lbl.setProperty("class", "dim")
        text_col.addWidget(s_lbl)
        
        layout.addLayout(text_col, 1)
        
        nav_btn = QPushButton()
        nav_btn.setIcon(get_material_icon("arrow_forward", icon_color, 20))
        nav_btn.setProperty("class", "flat")
        nav_btn.setFixedSize(36, 36)
        nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        nav_btn.clicked.connect(callback)
        layout.addWidget(nav_btn)
        
        return card

    def _navigate_to(self, index: int) -> None:
        if not self._selected_server_id:
            QMessageBox.information(self, "Files", "Please select a server first.")
            return
            
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return

        self._files_stack.setCurrentIndex(index)
        
        # Trigger load data on the respective page
        page = self._files_stack.currentWidget()
        if hasattr(page, "load_server"):
            page.load_server(info)

    def _navigate_home(self) -> None:
        self._files_stack.setCurrentIndex(0)

    def _open_server_folder(self) -> None:
        if not self._selected_server_id:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if info:
            _open_path(Path(info.server_dir))

    def _open_mods_folder(self) -> None:
        if not self._selected_server_id:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if info:
            mods = Path(info.server_dir) / "mods"
            mods.mkdir(parents=True, exist_ok=True)
            _open_path(mods)

    def _refresh_files(self, info: ServerInfo) -> None:
        # If a child page is active, refresh it.
        # Otherwise, no immediate action needed until navigated.
        idx = self._files_stack.currentIndex()
        if idx > 0:
            page = self._files_stack.currentWidget()
            if hasattr(page, "load_server"):
                page.load_server(info)

    def _on_check_mod_updates(self) -> None:
        """Check for mod updates — placeholder that navigates to Modrinth."""
        if not self._selected_server_id:
            QMessageBox.information(self._tabs, "Mod Updates", "Please select a server first.")
            return
        # For now, navigate to Modrinth page where the user can search for updates
        self._navigate_to(4)
