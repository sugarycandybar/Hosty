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

class VersionsWorker(QThread):
    loaded = Signal(object, object)
    failed = Signal(str)

    def __init__(self, server_manager: ServerManager):
        super().__init__()
        self._server_manager = server_manager

    def run(self) -> None:
        try:
            games = self._server_manager.download_manager.fetch_game_versions()
            loaders = self._server_manager.download_manager.fetch_loader_versions()
            self.loaded.emit(games, loaders)
        except Exception as exc:
            self.failed.emit(str(exc))


class InstallWorker(QThread):
    progress = Signal(float, str, str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        server_manager: ServerManager,
        name: str,
        mc_version: str,
        loader_version: str,
        ram_mb: int,
    ):
        super().__init__()
        self._server_manager = server_manager
        self._name = name
        self._mc_version = mc_version
        self._loader_version = loader_version
        self._ram_mb = ram_mb

    def _emit_progress(self, fraction: float, title: str, detail: str = "") -> None:
        self.progress.emit(min(1.0, max(0.0, fraction)), title, detail)

    def run(self) -> None:
        try:
            java_ver = get_required_java_version(self._mc_version)
            java_mgr = self._server_manager.java_manager
            dl_mgr = self._server_manager.download_manager

            if not java_mgr.is_java_available(java_ver):
                self._emit_progress(0.05, f"Downloading Java {java_ver} runtime")
                ok, msg = java_mgr.download_jre_sync(
                    java_ver,
                    progress_callback=lambda frac, text: self._emit_progress(
                        0.05 + frac * 0.30,
                        text,
                        f"Java {java_ver}",
                    ),
                )
                if not ok:
                    self.failed.emit(f"Failed to download Java {java_ver}: {msg}")
                    return

            self._emit_progress(0.40, "Downloading Fabric installer")
            installer_path = dl_mgr.download_installer(
                progress_callback=lambda frac, text: self._emit_progress(
                    0.40 + frac * 0.15,
                    text,
                )
            )
            if not installer_path:
                self.failed.emit("Failed to download Fabric installer")
                return

            self._emit_progress(0.58, "Creating server directory")
            server_info = self._server_manager.add_server(
                name=self._name,
                mc_version=self._mc_version,
                loader_version=self._loader_version,
                ram_mb=self._ram_mb,
            )

            self._emit_progress(0.62, "Downloading server.jar", self._mc_version)
            ok, msg = dl_mgr.download_server_jar(
                mc_version=self._mc_version,
                server_dir=str(server_info.server_dir),
                progress_callback=lambda frac, text: self._emit_progress(
                    0.62 + frac * 0.10,
                    text,
                ),
            )
            if not ok:
                self.failed.emit(f"Failed to download server.jar: {msg}")
                return

            self._emit_progress(0.74, "Installing Fabric", self._mc_version)
            java_path = java_mgr.get_java_path(java_ver) or java_mgr.get_java_for_mc(self._mc_version) or "java"
            ok, msg = dl_mgr.install_fabric_server(
                java_path=java_path,
                installer_jar=installer_path,
                mc_version=self._mc_version,
                server_dir=str(server_info.server_dir),
                loader_version=self._loader_version if self._loader_version else None,
                progress_callback=lambda frac, text: self._emit_progress(
                    0.74 + frac * 0.22,
                    text,
                ),
            )
            if not ok:
                self.failed.emit(f"Fabric installation failed: {msg}")
                return

            self._emit_progress(0.98, "Accepting EULA")
            config = ConfigManager(server_info.server_dir)
            config.set_eula(True)

            self._emit_progress(1.0, "Server created successfully")
            self.completed.emit(server_info.id)
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class CreateServerDialog(QDialog):
    server_created = Signal(str)

    def __init__(self, server_manager: ServerManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._server_manager = server_manager
        self._game_versions: list[str] = []
        self._loader_versions: list[str] = []
        self._install_worker: Optional[InstallWorker] = None
        self._versions_worker: Optional[VersionsWorker] = None

        self.setWindowTitle("Create Server")
        self.resize(560, 380)

        root = QVBoxLayout(self)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack)

        self._config_page = QWidget(self)
        self._progress_page = QWidget(self)
        self._stack.addWidget(self._config_page)
        self._stack.addWidget(self._progress_page)

        self._build_config_page()
        self._build_progress_page()

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        self._buttons.accepted.connect(self._on_create)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._fetch_versions()

    def _build_config_page(self) -> None:
        layout = QFormLayout(self._config_page)

        self._name_input = QLineEdit("My Server", self._config_page)
        self._name_input.textChanged.connect(self._validate)
        layout.addRow("Server name", self._name_input)

        self._mc_combo = QComboBox(self._config_page)
        self._mc_combo.addItem("Loading versions...")
        self._mc_combo.setEnabled(False)
        self._mc_combo.currentIndexChanged.connect(self._on_mc_changed)
        layout.addRow("Minecraft version", self._mc_combo)

        self._loader_label = QLabel("Latest (auto)", self._config_page)
        layout.addRow("Fabric loader", self._loader_label)

        self._java_label = QLabel("Detecting...", self._config_page)
        layout.addRow("Java runtime", self._java_label)

        self._ram_spin = QSpinBox(self._config_page)
        self._ram_spin.setRange(MIN_RAM_MB, MAX_RAM_MB)
        self._ram_spin.setSingleStep(256)
        self._ram_spin.setValue(DEFAULT_RAM_MB)
        self._ram_spin.setSuffix(" MB")
        layout.addRow("RAM", self._ram_spin)

    def _build_progress_page(self) -> None:
        layout = QVBoxLayout(self._progress_page)
        self._progress_title = QLabel("Creating server...", self._progress_page)
        self._progress_detail = QLabel("", self._progress_page)
        self._progress_bar = QProgressBar(self._progress_page)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_title)
        layout.addWidget(self._progress_detail)
        layout.addWidget(self._progress_bar)
        layout.addStretch(1)

    def _fetch_versions(self) -> None:
        self._versions_worker = VersionsWorker(self._server_manager)
        self._versions_worker.loaded.connect(self._on_versions_loaded)
        self._versions_worker.failed.connect(self._on_versions_failed)
        self._versions_worker.start()

    @Slot(object, object)
    def _on_versions_loaded(self, games, loaders) -> None:
        self._game_versions = list(games)
        self._loader_versions = list(loaders)

        self._mc_combo.clear()
        if self._game_versions:
            self._mc_combo.addItems(self._game_versions)
            self._mc_combo.setEnabled(True)
        else:
            self._mc_combo.addItem("No versions found")
            self._mc_combo.setEnabled(False)

        if self._loader_versions:
            self._loader_label.setText(f"Latest: {self._loader_versions[0]}")
        else:
            self._loader_label.setText("Unknown")

        self._on_mc_changed(self._mc_combo.currentIndex())
        self._validate()

    @Slot(str)
    def _on_versions_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Version Fetch Failed", message)
        self._mc_combo.clear()
        self._mc_combo.addItem("Unable to fetch versions")
        self._mc_combo.setEnabled(False)
        self._validate()

    @Slot(int)
    def _on_mc_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._game_versions):
            self._java_label.setText("Unknown")
            return

        mc_ver = self._game_versions[index]
        required = get_required_java_version(mc_ver)
        java_mgr = self._server_manager.java_manager
        if java_mgr.is_java_available(required):
            self._java_label.setText(f"Java {required} available")
        elif java_mgr.system_java_version and java_mgr.system_java_version >= required:
            self._java_label.setText(
                f"Java {required} required, system Java {java_mgr.system_java_version} is usable"
            )
        else:
            self._java_label.setText(f"Java {required} will be downloaded")

    def _validate(self) -> None:
        ready = bool(self._name_input.text().strip()) and bool(self._game_versions)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready)

    def _on_create(self) -> None:
        idx = self._mc_combo.currentIndex()
        if idx < 0 or idx >= len(self._game_versions):
            return

        name = self._name_input.text().strip()
        if not name:
            return

        mc_version = self._game_versions[idx]
        loader_version = self._loader_versions[0] if self._loader_versions else ""

        self._stack.setCurrentWidget(self._progress_page)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(False)

        self._install_worker = InstallWorker(
            self._server_manager,
            name,
            mc_version,
            loader_version,
            int(self._ram_spin.value()),
        )
        self._install_worker.progress.connect(self._on_progress)
        self._install_worker.completed.connect(self._on_completed)
        self._install_worker.failed.connect(self._on_failed)
        self._install_worker.start()

    @Slot(float, str, str)
    def _on_progress(self, fraction: float, title: str, detail: str) -> None:
        self._progress_title.setText(title)
        self._progress_detail.setText(detail)
        self._progress_bar.setValue(int(fraction * 100))

    @Slot(str)
    def _on_completed(self, server_id: str) -> None:
        self.server_created.emit(server_id)
        self.accept()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Create Server Failed", message)
        self._stack.setCurrentWidget(self._config_page)
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(True)
        self._validate()


