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


def _open_path(path: Path) -> bool:
    target = path.resolve()
    if target.is_file():
        target = target.parent
    try:
        os.startfile(str(target))
        return True
    except Exception:
        return False


def _format_uptime(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _iter_world_dirs(server_root: Path) -> list[Path]:
    if not server_root.is_dir():
        return []
    worlds = []
    for item in server_root.iterdir():
        if item.is_dir() and (item / "level.dat").exists():
            worlds.append(item)
    return sorted(worlds, key=lambda p: p.name.lower())


def _status_prefix(status: str) -> str:
    mapping = {
        ServerStatus.RUNNING: "[RUNNING]",
        ServerStatus.STARTING: "[STARTING]",
        ServerStatus.STOPPING: "[STOPPING]",
        ServerStatus.STOPPED: "[STOPPED]",
    }
    return mapping.get(status, "[UNKNOWN]")


class _MainThreadInvoker(QObject):
    invoke = Signal(object, object, object)

    def __init__(self) -> None:
        super().__init__()
        self.invoke.connect(self._run)

    @Slot(object, object, object)
    def _run(self, callback, args, kwargs) -> None:
        callback(*args, **kwargs)


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


class HostyMainWindow(QMainWindow):
    def __init__(self, server_manager: ServerManager):
        super().__init__()
        self._server_manager = server_manager
        self._selected_server_id: Optional[str] = None
        self._selected_process = None
        self._status_handler_id = None
        self._output_handler_id = None
        self._process_start_ts: Optional[float] = None
        self._ignore_list_events = False

        self.setWindowTitle("Hosty")
        self.resize(1200, 760)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal, root)
        layout.addWidget(splitter)

        self._build_sidebar(splitter)
        self._build_content(splitter)
        splitter.setSizes([330, 870])

        self._manager_added_id = self._server_manager.connect("server-added", self._on_server_added)
        self._manager_removed_id = self._server_manager.connect("server-removed", self._on_server_removed)
        self._manager_changed_id = self._server_manager.connect("server-changed", self._on_server_changed)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._on_stats_tick)
        self._stats_timer.start()

        self._populate_servers()

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

    def _build_content(self, splitter: QSplitter) -> None:
        content = QWidget(splitter)
        layout = QVBoxLayout(content)

        header = QHBoxLayout()
        self._title_label = QLabel("Select a server", content)
        self._title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(self._title_label)
        header.addStretch(1)
        self._toggle_btn = QPushButton("Start", content)
        self._toggle_btn.setEnabled(False)
        self._toggle_btn.clicked.connect(self._toggle_server)
        header.addWidget(self._toggle_btn)
        layout.addLayout(header)

        self._tabs = QTabWidget(content)
        layout.addWidget(self._tabs)

        self._build_console_tab()
        self._build_performance_tab()
        self._build_properties_tab()
        self._build_files_tab()

    def _build_console_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)

        self._console_output = QPlainTextEdit(tab)
        self._console_output.setReadOnly(True)
        layout.addWidget(self._console_output)

        row = QHBoxLayout()

        self._command_input = QLineEdit(tab)
        self._command_input.setPlaceholderText("Type a command...")
        self._command_input.returnPressed.connect(self._send_command)
        row.addWidget(self._command_input)

        clear_btn = QPushButton("Clear", tab)
        clear_btn.clicked.connect(self._console_output.clear)
        row.addWidget(clear_btn)

        send_btn = QPushButton("Send", tab)
        send_btn.clicked.connect(self._send_command)
        row.addWidget(send_btn)

        layout.addLayout(row)
        self._tabs.addTab(tab, "Console")

    def _build_performance_tab(self) -> None:
        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        layout = QFormLayout()

        self._perf_status = QLabel("Stopped", tab)
        self._perf_pid = QLabel("-", tab)
        self._perf_uptime = QLabel("-", tab)
        self._perf_cpu = QLabel("-", tab)
        self._perf_ram = QLabel("-", tab)
        self._perf_cpu_bar = QProgressBar(tab)
        self._perf_cpu_bar.setRange(0, 100)
        self._perf_cpu_bar.setValue(0)
        self._perf_ram_bar = QProgressBar(tab)
        self._perf_ram_bar.setRange(0, 100)
        self._perf_ram_bar.setValue(0)

        layout.addRow("Status", self._perf_status)
        layout.addRow("PID", self._perf_pid)
        layout.addRow("Uptime", self._perf_uptime)
        layout.addRow("CPU", self._perf_cpu)
        layout.addRow("CPU Load", self._perf_cpu_bar)
        layout.addRow("RAM", self._perf_ram)
        layout.addRow("RAM Usage", self._perf_ram_bar)

        outer.addLayout(layout)
        outer.addStretch(1)

        self._tabs.addTab(tab, "Performance")

    def _build_properties_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)

        self._properties_editor = QTextEdit(tab)
        self._properties_editor.setAcceptRichText(False)
        layout.addWidget(self._properties_editor)

        save_btn = QPushButton("Save Properties", tab)
        save_btn.clicked.connect(self._save_properties)
        layout.addWidget(save_btn)

        self._tabs.addTab(tab, "Properties")

    def _build_files_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)

        btn_row = QHBoxLayout()
        open_server = QPushButton("Open Server Folder", tab)
        open_mods = QPushButton("Open Mods Folder", tab)
        open_server.clicked.connect(self._open_server_folder)
        open_mods.clicked.connect(self._open_mods_folder)
        btn_row.addWidget(open_server)
        btn_row.addWidget(open_mods)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._worlds_label = QLabel("Worlds", tab)
        layout.addWidget(self._worlds_label)

        self._world_list = QListWidget(tab)
        self._world_list.itemDoubleClicked.connect(self._open_selected_world)
        layout.addWidget(self._world_list)

        open_world_btn = QPushButton("Open Selected World", tab)
        open_world_btn.clicked.connect(self._open_selected_world)
        layout.addWidget(open_world_btn)

        self._tabs.addTab(tab, "Files")

    def _on_stats_tick(self) -> None:
        self._refresh_server_rows_status()
        self._refresh_performance()

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

    @Slot(object, str)
    def _on_server_added(self, _manager, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return
        self._add_or_update_item(info)
        self._select_server(server_id)

    @Slot(object, str)
    def _on_server_removed(self, _manager, server_id: str) -> None:
        for i in range(self._server_list.count()):
            item = self._server_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == server_id:
                self._server_list.takeItem(i)
                break

        if self._server_list.count() == 0:
            self._clear_selection_state()

    @Slot(object, str)
    def _on_server_changed(self, _manager, server_id: str) -> None:
        info = self._server_manager.get_server(server_id)
        if not info:
            return
        self._add_or_update_item(info)
        if self._selected_server_id == server_id:
            self._load_server(info)

    @Slot(QListWidgetItem, QListWidgetItem)
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

    @Slot(object, str)
    def _on_process_status(self, _process, status: str) -> None:
        if status in (ServerStatus.STARTING, ServerStatus.RUNNING):
            if self._process_start_ts is None:
                self._process_start_ts = time.time()
        elif status == ServerStatus.STOPPED:
            self._process_start_ts = None

        self._refresh_toggle_button()
        self._refresh_performance()
        self._refresh_server_rows_status()

    @Slot(object, str)
    def _on_process_output(self, _process, text: str) -> None:
        self._console_output.appendPlainText(text.rstrip("\n"))
        scrollbar = self._console_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

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

    def _send_command(self) -> None:
        text = self._command_input.text().strip()
        if not text:
            return

        if self._selected_process:
            self._console_output.appendPlainText(f"> {text}")
            self._selected_process.send_command(text)
        else:
            self._console_output.appendPlainText("[Hosty] No process connected")
        self._command_input.clear()

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

    def _load_properties(self, info: ServerInfo) -> None:
        config = self._server_manager.get_config(info.id)
        if not config:
            self._properties_editor.clear()
            return

        props = dict(DEFAULT_SERVER_PROPERTIES)
        props.update(config.load())
        lines = [f"{k}={v}" for k, v in props.items()]
        self._properties_editor.setPlainText("\n".join(lines) + "\n")

    def _save_properties(self) -> None:
        if not self._selected_server_id:
            return
        config = self._server_manager.get_config(self._selected_server_id)
        if not config:
            return

        raw = self._properties_editor.toPlainText().splitlines()
        for line in raw:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config.set_value(key.strip(), value.strip())
        config.save()

        self._server_manager.emit_on_main_thread("server-changed", self._selected_server_id)
        QMessageBox.information(self, "Properties", "server.properties saved")

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
            self._world_list.addItem(world.name)

    def _open_selected_world(self, *_args) -> None:
        if not self._selected_server_id:
            return
        item = self._world_list.currentItem()
        if not item:
            return
        info = self._server_manager.get_server(self._selected_server_id)
        if not info:
            return

        world_dir = Path(info.server_dir) / item.text()
        if not _open_path(world_dir):
            QMessageBox.warning(self, "Open World", "Could not open selected world folder")

    def _refresh_performance(self) -> None:
        process = self._selected_process
        if not process:
            self._perf_status.setText("Stopped")
            self._perf_pid.setText("-")
            self._perf_uptime.setText("-")
            self._perf_cpu.setText("-")
            self._perf_ram.setText("-")
            self._perf_cpu_bar.setValue(0)
            self._perf_ram_bar.setValue(0)
            return

        status = process.status
        self._perf_status.setText(status.capitalize())

        pid = process.pid
        self._perf_pid.setText(str(pid) if pid else "-")

        if process.is_running and self._process_start_ts is not None:
            self._perf_uptime.setText(_format_uptime(time.time() - self._process_start_ts))
        else:
            self._perf_uptime.setText("-")

        if HAS_PSUTIL and pid and process.is_running:
            try:
                p = psutil.Process(pid)
                cpu = p.cpu_percent(interval=None)
                ram_mb = p.memory_info().rss / (1024 * 1024)
                ram_pct = min(100.0, (ram_mb / max(1.0, float(process.ram_mb))) * 100.0)
                self._perf_cpu.setText(f"{cpu:.1f}%")
                self._perf_ram.setText(f"{ram_mb:.1f} MB")
                self._perf_cpu_bar.setValue(int(max(0.0, min(100.0, cpu))))
                self._perf_ram_bar.setValue(int(ram_pct))
                return
            except Exception:
                pass

        self._perf_cpu.setText("N/A")
        self._perf_ram.setText("N/A")
        self._perf_cpu_bar.setValue(0)
        self._perf_ram_bar.setValue(0)


class HostyWindowsApplication:
    """Hosty frontend for Windows, implemented with PySide6."""

    def run(self, argv: list[str]) -> int:
        app = QApplication(argv)
        app.setApplicationName("Hosty")
        app.setStyleSheet(
            "QMainWindow { background: #171a21; }"
            "QWidget { color: #e8ecf1; font-size: 13px; }"
            "QPlainTextEdit, QTextEdit, QListWidget { background: #11141a; border: 1px solid #2c3340; border-radius: 6px; }"
            "QPushButton { background: #2b6cb0; border: none; border-radius: 6px; padding: 6px 10px; }"
            "QPushButton:disabled { background: #3a4354; color: #9aa6bd; }"
            "QTabWidget::pane { border: 1px solid #2c3340; border-radius: 6px; }"
            "QTabBar::tab { background: #1d2230; padding: 6px 10px; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #2b6cb0; }"
        )

        invoker = _MainThreadInvoker()
        set_main_thread_dispatcher(
            lambda callback, *args, **kwargs: invoker.invoke.emit(callback, args, kwargs)
        )

        server_manager = ServerManager()
        window = HostyMainWindow(server_manager)
        window.show()

        try:
            return app.exec()
        finally:
            try:
                server_manager.stop_all()
            except Exception:
                pass
            set_main_thread_dispatcher(None)
