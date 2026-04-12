"""
Modrinth page for the Files tab.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QScrollArea,
)
from ..components import SmoothScrollArea

from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.backend import modrinth_client as modrinth
from hosty.core.events import dispatch_on_main_thread


class ModrinthPage(QWidget):
    def __init__(self, server_manager: ServerManager, back_callback):
        super().__init__()
        self._server_manager = server_manager
        self._server_info: Optional[ServerInfo] = None
        self._search_thread = None
        self._install_thread = None

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

        title = QLabel("Modrinth")
        title.setProperty("class", "title")
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(header)

        # Search Filters
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(24, 16, 24, 0)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search mods and modpacks...")
        filters_layout.addWidget(self._search_input, 1)

        self._type_combo = QComboBox()
        self._type_combo.addItems(["Mods", "Modpacks"])
        filters_layout.addWidget(self._type_combo)

        layout.addWidget(filters)

        # Progress bar for installs
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._status_lbl = QLabel("")
        self._status_lbl.setProperty("class", "dim")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_lbl)

        # Search Results Context
        self._scroll = SmoothScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(24, 16, 24, 24)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, 1)

        # Debounce timer
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(500)
        self._search_timer.timeout.connect(self._perform_search)

        self._search_input.textChanged.connect(lambda text: self._search_timer.start())
        self._type_combo.currentIndexChanged.connect(lambda idx: self._search_timer.start())

    def load_server(self, info: ServerInfo) -> None:
        if self._server_info and self._server_info.id == info.id:
            return
        self._server_info = info
        self._perform_search()

    def _perform_search(self) -> None:
        if not self._server_info:
            return

        query = self._search_input.text().strip()
        project_type = "mod" if self._type_combo.currentIndex() == 0 else "modpack"
        game_version = self._server_info.mc_version

        # Clear existing
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        lbl = QLabel("Searching...")
        lbl.setProperty("class", "dim")
        self._content_layout.addWidget(lbl)

        def worker():
            try:
                hits, _ = modrinth.search_mods(
                    query=query,
                    limit=20,
                    game_version=game_version,
                    project_type=project_type,
                    server_side_only=True
                )
                dispatch_on_main_thread(lambda: self._populate_results(hits, project_type))
            except Exception as e:
                err_msg = str(e)
                dispatch_on_main_thread(lambda: self._show_search_error(err_msg))

        self._search_thread = threading.Thread(target=worker, daemon=True)
        self._search_thread.start()

    def _show_search_error(self, err: str) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        lbl = QLabel(f"Search failed: {err}")
        lbl.setProperty("class", "error_tag")
        self._content_layout.addWidget(lbl)

    def _populate_results(self, hits: list[modrinth.ModrinthHit], project_type: str) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not hits:
            lbl = QLabel(f"No {project_type}s found for MC {self._server_info.mc_version}.")
            lbl.setProperty("class", "dim")
            self._content_layout.addWidget(lbl)
            return

        for hit in hits:
            self._content_layout.addWidget(self._build_result_card(hit))

    def _build_result_card(self, hit: modrinth.ModrinthHit) -> QWidget:
        card = QWidget()
        card.setProperty("class", "card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        
        title = QLabel(f"{hit.title}")
        title.setProperty("class", "title")
        text_col.addWidget(title)

        desc = QLabel(hit.description)
        desc.setProperty("class", "dim")
        desc.setWordWrap(True)
        text_col.addWidget(desc)

        layout.addLayout(text_col, 1)

        install_btn = QPushButton("Install")
        install_btn.setProperty("class", "accent")
        install_btn.clicked.connect(lambda *_args, h=hit: self._install_hit(h))
        layout.addWidget(install_btn)

        return card

    def _install_hit(self, hit: modrinth.ModrinthHit) -> None:
        if not self._server_info:
            return

        proc = self._server_manager.get_process(self._server_info.id)
        if proc and proc.is_running:
            QMessageBox.warning(self, "Server Running", "Please stop the server before installing mods.")
            return

        if self._progress_bar.isVisible():
            QMessageBox.warning(self, "Busy", "An installation is already in progress.")
            return

        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._status_lbl.setText(f"Preparing to install {hit.title}...")

        def worker():
            try:

                # Fetch best version for MC
                version_info = modrinth.find_compatible_version_file(
                    hit.project_id, self._server_info.mc_version, "fabric"
                )
                if not version_info:
                    raise Exception("No compatible version for this Minecraft version.")

                url, filename = version_info
                server_dir = self._server_info.server_dir

                if hit.project_type == "modpack":
                    # We need the version_id of the packed file. We have to fetch the single best version again to get its id.
                    best_ver = modrinth.find_compatible_version(hit.project_id, self._server_info.mc_version, "fabric")
                    if not best_ver:
                        raise Exception("No compatible modpack version.")
                    
                    def cb(progress, total, name):
                        rate = int((progress / max(total, 1)) * 100)
                        dispatch_on_main_thread(lambda: self._progress_bar.setValue(rate))
                        dispatch_on_main_thread(lambda: self._status_lbl.setText(f"Extracting {name} ({progress}/{total})"))

                    modrinth.install_modpack(best_ver.version_id, server_dir, progress_callback=cb)
                else:
                    # Single mod
                    dispatch_on_main_thread(lambda: self._status_lbl.setText("Downloading mod file..."))
                    mod_dir = server_dir / "mods"
                    mod_dir.mkdir(exist_ok=True)
                    modrinth.download_to(url, mod_dir / filename)

                    # Auto dependencies if enabled
                    if self._server_manager.preferences.auto_resolve_mod_dependencies:
                        dispatch_on_main_thread(lambda: self._status_lbl.setText("Resolving dependencies..."))
                        best_ver = modrinth.find_compatible_version(hit.project_id, self._server_info.mc_version, "fabric")
                        if best_ver:
                            deps = modrinth.resolve_required_dependencies(
                                best_ver.version_id, self._server_info.mc_version, "fabric"
                            )
                            for i, dep in enumerate(deps):
                                dispatch_on_main_thread(lambda: self._status_lbl.setText(f"Downloading {dep.name}..."))
                                mod_dir = server_dir / "mods"
                                try:
                                    modrinth.download_to(dep.download_url, mod_dir / dep.filename)
                                except Exception:
                                    pass

                dispatch_on_main_thread(lambda: self._finish_install(hit.title, True, ""))
            except Exception as e:
                err_msg = str(e)
                dispatch_on_main_thread(lambda: self._finish_install(hit.title, False, err_msg))
            finally:
                pass

        self._install_thread = threading.Thread(target=worker, daemon=True)
        self._install_thread.start()

    def _finish_install(self, name: str, ok: bool, msg: str) -> None:
        self._progress_bar.setVisible(False)
        self._status_lbl.setText("")
        
        if ok:
            QMessageBox.information(self, "Success", f"Successfully installed '{name}'.")
        else:
            QMessageBox.warning(self, "Error", f"Failed to install '{name}': {msg}")
