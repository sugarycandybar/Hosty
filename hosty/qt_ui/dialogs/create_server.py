"""
Multi-step Create Server dialog for Hosty Windows UI.

Step 1 (Details): Name, seed, icon, EULA
Step 2 (Runtime): MC version, loader, Java info, RAM, optimization mods
Progress: animated progress bar with status text
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hosty.shared.backend.config_manager import ConfigManager
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.utils.constants import (
    DEFAULT_RAM_MB,
    DEFAULT_SERVER_PROPERTIES,
    MAX_RAM_MB,
    MIN_RAM_MB,
    get_required_java_version,
)


OPTIMISATION_MODS = [
    ("lithium", "Lithium"),
    ("ferrite-core", "FerriteCore"),
    ("c2me-fabric", "Concurrent Chunk Management Engine"),
    ("fast-noise", "Fast Noise"),
    ("vmp-fabric", "Very Many Players"),
    ("scalablelux", "ScalableLux"),
    ("krypton", "Krypton"),
    ("modernfix", "ModernFix"),
]


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
        seed: str = "",
        eula: bool = True,
        icon_path: str = "",
        install_optimisations: bool = False,
    ):
        super().__init__()
        self._server_manager = server_manager
        self._name = name
        self._mc_version = mc_version
        self._loader_version = loader_version
        self._ram_mb = ram_mb
        self._seed = seed
        self._eula = eula
        self._icon_path = icon_path
        self._install_optimisations = install_optimisations

    def _emit_progress(self, fraction: float, title: str, detail: str = "") -> None:
        self.progress.emit(min(1.0, max(0.0, fraction)), title, detail)

    def run(self) -> None:
        try:
            java_ver = get_required_java_version(self._mc_version)
            java_mgr = self._server_manager.java_manager
            dl_mgr = self._server_manager.download_manager

            # Step 1: Java
            if not java_mgr.is_java_available(java_ver):
                self._emit_progress(0.05, f"Downloading Java {java_ver} runtime")
                ok, msg = java_mgr.download_jre_sync(
                    java_ver,
                    progress_callback=lambda frac, text: self._emit_progress(
                        0.05 + frac * 0.20, text, f"Java {java_ver}",
                    ),
                )
                if not ok:
                    self.failed.emit(f"Failed to download Java {java_ver}: {msg}")
                    return

            # Step 2: Fabric installer
            self._emit_progress(0.28, "Downloading Fabric installer")
            installer_path = dl_mgr.download_installer(
                progress_callback=lambda frac, text: self._emit_progress(
                    0.28 + frac * 0.14, text,
                )
            )
            if not installer_path:
                self.failed.emit("Failed to download Fabric installer")
                return

            # Step 3: Create server entry
            self._emit_progress(0.44, "Creating server directory")
            server_info = self._server_manager.add_server(
                name=self._name,
                mc_version=self._mc_version,
                loader_version=self._loader_version,
                ram_mb=self._ram_mb,
            )

            # Step 4: Download server.jar
            self._emit_progress(0.48, "Downloading Minecraft server.jar", self._mc_version)
            ok, msg = dl_mgr.download_server_jar(
                mc_version=self._mc_version,
                server_dir=str(server_info.server_dir),
                progress_callback=lambda frac, text: self._emit_progress(
                    0.48 + frac * 0.12, text,
                ),
            )
            if not ok:
                self.failed.emit(f"Failed to download server.jar: {msg}")
                return

            # Step 5: Install Fabric
            self._emit_progress(0.62, "Installing Fabric server", self._mc_version)
            java_path = java_mgr.get_java_path(java_ver) or java_mgr.get_java_for_mc(self._mc_version) or "java"
            ok, msg = dl_mgr.install_fabric_server(
                java_path=java_path,
                installer_jar=installer_path,
                mc_version=self._mc_version,
                server_dir=str(server_info.server_dir),
                loader_version=self._loader_version if self._loader_version else None,
                progress_callback=lambda frac, text: self._emit_progress(
                    0.62 + frac * 0.22, text,
                ),
            )
            if not ok:
                self.failed.emit(f"Fabric installation failed: {msg}")
                return

            # Step 6: Configure
            self._emit_progress(0.86, "Applying server settings")
            config = ConfigManager(str(server_info.server_dir))
            config.load()
            config.set_value("motd", DEFAULT_SERVER_PROPERTIES.get("motd", "a hosty server"))
            if self._seed:
                config.set_value("level-seed", self._seed)
            config.save()
            config.set_eula(self._eula)

            # Step 7: Icon
            if self._icon_path:
                self._emit_progress(0.90, "Applying server icon")
                try:
                    from hosty.shared.utils.image_utils import convert_to_png
                    icon_output = server_info.server_dir / "icon.png"
                    convert_to_png(self._icon_path, str(icon_output), size=128)
                    self._server_manager.set_server_icon(server_info.id, str(icon_output))
                except Exception:
                    pass

            # Step 8: Optimization mods
            if self._install_optimisations:
                self._emit_progress(0.92, "Installing server-optimising mods", "0/0")
                self._install_optimising_mods(server_info.server_dir, self._mc_version)

            self._emit_progress(1.0, "Server created successfully")
            self.completed.emit(server_info.id)
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")

    def _install_optimising_mods(self, server_dir: Path, mc_version: str) -> None:
        from hosty.shared.backend import modrinth_client

        mods_dir = Path(server_dir) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        installed = {p.name.lower() for p in mods_dir.glob("*.jar")}

        total = len(OPTIMISATION_MODS)
        done = 0
        for slug, title in OPTIMISATION_MODS:
            done += 1
            progress = 0.92 + (done / max(1, total)) * 0.06
            self._emit_progress(progress, "Installing server-optimising mods", f"{done}/{total} · {title}")
            try:
                versions = modrinth_client.get_project_versions(slug)
                if not versions:
                    continue
                version = None
                for v in versions:
                    has_mc = mc_version in (v.game_versions or [])
                    has_loader = "fabric" in [x.lower() for x in (v.loaders or [])]
                    if has_mc and has_loader:
                        version = v
                        break
                if not version:
                    continue
                if version.filename.lower() in installed:
                    continue
                modrinth_client.download_to(version.download_url, mods_dir / version.filename)
                installed.add(version.filename.lower())
            except Exception:
                continue


class CreateServerDialog(QDialog):
    """Multi-step create server dialog."""

    server_created = Signal(str)

    def __init__(self, server_manager: ServerManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._server_manager = server_manager
        self._game_versions: list[str] = []
        self._loader_versions: list[str] = []
        self._install_worker: Optional[InstallWorker] = None
        self._versions_worker: Optional[VersionsWorker] = None
        self._icon_source_path: str = ""

        self.setWindowTitle("Create Server")
        self.setMinimumSize(540, 480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        self._details_page = QWidget(self)
        self._runtime_page = QWidget(self)
        self._progress_page = QWidget(self)
        self._stack.addWidget(self._details_page)
        self._stack.addWidget(self._runtime_page)
        self._stack.addWidget(self._progress_page)

        self._build_details_page()
        self._build_runtime_page()
        self._build_progress_page()

        # Bottom buttons
        btn_bar = QWidget(self)
        btn_bar.setContentsMargins(20, 12, 20, 16)
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)

        btn_layout.addStretch()

        self._primary_btn = QPushButton("Next")
        self._primary_btn.setProperty("class", "accent")
        self._primary_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary_btn.setEnabled(False)
        self._primary_btn.clicked.connect(self._on_primary)
        btn_layout.addWidget(self._primary_btn)

        root.addWidget(btn_bar)

        self._fetch_versions()

    def _build_details_page(self) -> None:
        layout = QVBoxLayout(self._details_page)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Server Details")
        title.setProperty("class", "header")
        layout.addWidget(title)

        subtitle = QLabel("Name your server and configure initial settings")
        subtitle.setProperty("class", "subtitle")
        layout.addWidget(subtitle)

        # Server info group
        info_group = QGroupBox("Server Info")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(10)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Server Name"))
        self._name_input = QLineEdit("My Server")
        self._name_input.textChanged.connect(self._validate)
        name_row.addWidget(self._name_input, 1)
        info_layout.addLayout(name_row)

        seed_row = QHBoxLayout()
        seed_row.addWidget(QLabel("World Seed"))
        self._seed_input = QLineEdit()
        self._seed_input.setPlaceholderText("Leave blank for random")
        seed_row.addWidget(self._seed_input, 1)
        info_layout.addLayout(seed_row)

        icon_row = QHBoxLayout()
        icon_row.addWidget(QLabel("Server Icon"))
        self._icon_label = QLabel("No icon selected")
        self._icon_label.setProperty("class", "dim")
        icon_row.addWidget(self._icon_label, 1)
        icon_btn = QPushButton("Choose…")
        icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        icon_btn.clicked.connect(self._on_choose_icon)
        icon_row.addWidget(icon_btn)
        info_layout.addLayout(icon_row)

        layout.addWidget(info_group)

        # EULA group
        eula_group = QGroupBox("Minecraft EULA")
        eula_layout = QVBoxLayout(eula_group)

        self._eula_check = QCheckBox("I agree to the Minecraft EULA")
        self._eula_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eula_check.setToolTip("Required to complete server creation")
        self._eula_check.toggled.connect(self._validate)
        eula_layout.addWidget(self._eula_check)

        eula_info = QLabel("You must accept the Minecraft End User License Agreement to create a server.")
        eula_info.setWordWrap(True)
        eula_info.setProperty("class", "dim")
        eula_layout.addWidget(eula_info)

        layout.addWidget(eula_group)
        layout.addStretch()

    def _build_runtime_page(self) -> None:
        layout = QVBoxLayout(self._runtime_page)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Runtime Configuration")
        title.setProperty("class", "header")
        layout.addWidget(title)

        subtitle = QLabel("Choose Minecraft and Fabric versions")
        subtitle.setProperty("class", "subtitle")
        layout.addWidget(subtitle)

        # Versions group
        ver_group = QGroupBox("Runtime")
        ver_layout = QVBoxLayout(ver_group)
        ver_layout.setSpacing(10)

        mc_row = QHBoxLayout()
        mc_row.addWidget(QLabel("Minecraft version"))
        self._mc_combo = QComboBox()
        self._mc_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mc_combo.addItem("Loading versions...")
        self._mc_combo.setEnabled(False)
        self._mc_combo.currentIndexChanged.connect(self._on_mc_changed)
        mc_row.addWidget(self._mc_combo, 1)
        ver_layout.addLayout(mc_row)

        loader_row = QHBoxLayout()
        loader_row.addWidget(QLabel("Fabric loader"))
        self._loader_combo = QComboBox()
        self._loader_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._loader_combo.addItem("Loading...")
        self._loader_combo.setEnabled(False)
        loader_row.addWidget(self._loader_combo, 1)
        ver_layout.addLayout(loader_row)

        java_row = QHBoxLayout()
        java_row.addWidget(QLabel("Java runtime"))
        self._java_label = QLabel("Detecting...")
        self._java_label.setProperty("class", "dim")
        java_row.addWidget(self._java_label, 1)
        ver_layout.addLayout(java_row)

        layout.addWidget(ver_group)

        # Resources group
        res_group = QGroupBox("Resources")
        res_layout = QVBoxLayout(res_group)

        ram_row = QHBoxLayout()
        ram_row.addWidget(QLabel("RAM (MB)"))
        self._ram_spin = QSpinBox()
        self._ram_spin.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ram_spin.setRange(MIN_RAM_MB, MAX_RAM_MB)
        self._ram_spin.setSingleStep(256)
        self._ram_spin.setValue(self._server_manager.preferences.default_ram_mb)
        self._ram_spin.setSuffix(" MB")
        ram_row.addWidget(self._ram_spin, 1)
        res_layout.addLayout(ram_row)

        layout.addWidget(res_group)

        # Optional group
        opt_group = QGroupBox("Optional Setup")
        opt_layout = QVBoxLayout(opt_group)

        self._optimise_check = QCheckBox("Install server-optimising mods")
        self._optimise_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._optimise_check.setToolTip("Installs compatible performance mods from Modrinth")
        opt_layout.addWidget(self._optimise_check)

        layout.addWidget(opt_group)
        layout.addStretch()

    def _build_progress_page(self) -> None:
        layout = QVBoxLayout(self._progress_page)
        layout.setSpacing(16)
        layout.setContentsMargins(40, 60, 40, 40)

        self._progress_icon = QLabel("📦")
        self._progress_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_icon.setStyleSheet("font-size: 48px;")
        layout.addWidget(self._progress_icon)

        self._progress_title = QLabel("Creating Server")
        self._progress_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(self._progress_title)

        self._progress_detail = QLabel("Preparing...")
        self._progress_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_detail.setProperty("class", "subtitle")
        layout.addWidget(self._progress_detail)

        layout.addSpacing(12)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        layout.addWidget(self._progress_bar)

        self._progress_sub = QLabel("")
        self._progress_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_sub.setProperty("class", "dim")
        layout.addWidget(self._progress_sub)

        layout.addStretch()

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

        self._loader_combo.clear()
        if self._loader_versions:
            self._loader_combo.addItems(self._loader_versions)
            self._loader_combo.setEnabled(True)
        else:
            self._loader_combo.addItem("Unknown")
            self._loader_combo.setEnabled(False)

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
            self._java_label.setText(f"Java {required} ✓ Available")
        elif java_mgr.system_java_version and java_mgr.system_java_version >= required:
            self._java_label.setText(
                f"Java {required} required — system Java {java_mgr.system_java_version} usable"
            )
        else:
            self._java_label.setText(f"Java {required} — will be downloaded")

    def _on_choose_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Server Icon",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if path:
            self._icon_source_path = path
            self._icon_label.setText(Path(path).name)
            self._icon_label.setProperty("class", "")
            self._icon_label.setStyleSheet("")

    def _validate(self) -> None:
        page_idx = self._stack.currentIndex()

        if page_idx == 0:
            # Details page
            name_ok = bool(self._name_input.text().strip())
            eula_ok = self._eula_check.isChecked()
            self._primary_btn.setEnabled(name_ok and eula_ok)
            self._primary_btn.setText("Next")
            self._cancel_btn.setText("Cancel")
            self._cancel_btn.setEnabled(True)
        elif page_idx == 1:
            # Runtime page
            has_versions = len(self._game_versions) > 0
            has_loaders = len(self._loader_versions) > 0
            self._primary_btn.setEnabled(has_versions and has_loaders)
            self._primary_btn.setText("Create")
            self._cancel_btn.setText("Back")
            self._cancel_btn.setEnabled(True)
        else:
            # Progress page
            self._primary_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)

    def _on_cancel(self) -> None:
        page_idx = self._stack.currentIndex()
        if page_idx == 1:
            # Go back to details
            self._stack.setCurrentIndex(0)
            self._validate()
        else:
            self.reject()

    def _on_primary(self) -> None:
        page_idx = self._stack.currentIndex()
        if page_idx == 0:
            # Go to runtime page
            self._stack.setCurrentIndex(1)
            self._validate()
            return

        if page_idx != 1:
            return

        # Start creation
        idx = self._mc_combo.currentIndex()
        if idx < 0 or idx >= len(self._game_versions):
            return

        name = self._name_input.text().strip()
        if not name:
            return

        mc_version = self._game_versions[idx]
        loader_idx = self._loader_combo.currentIndex()
        loader_version = self._loader_versions[loader_idx] if loader_idx < len(self._loader_versions) else ""

        self._stack.setCurrentIndex(2)
        self._validate()

        self._install_worker = InstallWorker(
            self._server_manager,
            name,
            mc_version,
            loader_version,
            int(self._ram_spin.value()),
            seed=self._seed_input.text().strip(),
            eula=self._eula_check.isChecked(),
            icon_path=self._icon_source_path,
            install_optimisations=self._optimise_check.isChecked(),
        )
        self._install_worker.progress.connect(self._on_progress)
        self._install_worker.completed.connect(self._on_completed)
        self._install_worker.failed.connect(self._on_failed)
        self._install_worker.start()

    @Slot(float, str, str)
    def _on_progress(self, fraction: float, title: str, detail: str) -> None:
        self._progress_detail.setText(title)
        self._progress_sub.setText(detail)
        self._progress_bar.setValue(int(fraction * 100))

    @Slot(str)
    def _on_completed(self, server_id: str) -> None:
        self._progress_icon.setText("✅")
        self._progress_title.setText("Server Created!")
        self._progress_detail.setText("Your Fabric server is ready to start")
        self._progress_bar.setValue(100)
        self._progress_sub.setText("")

        # Auto close after 1.5s
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self._finish(server_id))

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._progress_icon.setText("❌")
        self._progress_title.setText("Creation Failed")
        self._progress_detail.setText(message)
        self._progress_bar.setValue(0)
        self._progress_sub.setText("Please try again")

        # Allow going back
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Back")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(lambda: self._retry())

    def _retry(self):
        self._stack.setCurrentIndex(0)
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._validate()

    def _finish(self, server_id: str):
        self.server_created.emit(server_id)
        self.accept()
