"""
Properties mixin — GUI editor for server.properties with auto-save.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QSpinBox,
    QProgressBar,
)

from ..components import SmoothScrollArea
from hosty.backend.config_manager import ConfigManager
from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.utils.constants import (
    DEFAULT_RAM_MB,
    DIFFICULTIES,
    GAMEMODES,
    LEVEL_TYPE_NAMES,
    LEVEL_TYPES,
    MAX_RAM_MB,
    MIN_RAM_MB,
    ServerStatus,
)


class PropertiesMixin:
    """Mixin providing a grouped GUI editor for server.properties."""

    def _build_properties_tab(self) -> None:
        self._prop_config: Optional[ConfigManager] = None
        self._prop_server_info: Optional[ServerInfo] = None
        
        self._suppress_prop_changes = False

        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Restart banner
        self._props_banner = QWidget(tab)
        self._props_banner.setVisible(False)
        self._props_banner.setStyleSheet(
            "background: rgba(229, 165, 10, 0.15); padding: 8px 16px;"
        )
        banner_layout = QHBoxLayout(self._props_banner)
        banner_layout.setContentsMargins(16, 6, 16, 6)
        banner_label = QLabel("⚠️ Restart the server to apply changes")
        banner_label.setStyleSheet("color: #e5a50a; font-weight: 600; font-size: 12px;")
        banner_layout.addWidget(banner_label)
        banner_layout.addStretch()
        dismiss_btn = QLabel("✕")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setStyleSheet("color: #e5a50a; font-weight: 700; padding: 0 4px;")
        dismiss_btn.mousePressEvent = lambda _: self._props_banner.setVisible(False)
        banner_layout.addWidget(dismiss_btn)
        outer.addWidget(self._props_banner)

        scroll = SmoothScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        self._prop_widgets = {}
        self._prop_config: Optional[ConfigManager] = None
        self._prop_server_info: Optional[ServerInfo] = None
        self._suppress_prop_changes = False

        # ===== General =====
        general = QGroupBox("General")
        gen_lay = QVBoxLayout(general)
        gen_lay.setSpacing(10)

        self._prop_widgets["motd"] = self._add_prop_entry(gen_lay, "Message of the Day", "motd", "a hosty server")
        self._prop_widgets["max-players"] = self._add_prop_spin(gen_lay, "Max Players", "max-players", 1, 1000, 20)
        self._prop_widgets["difficulty"] = self._add_prop_combo(gen_lay, "Difficulty", "difficulty", DIFFICULTIES, "easy")
        self._prop_widgets["gamemode"] = self._add_prop_combo(gen_lay, "Default Gamemode", "gamemode", GAMEMODES, "survival")

        layout.addWidget(general)

        # ===== Resources =====
        resources = QGroupBox("Resources")
        res_lay = QVBoxLayout(resources)
        res_lay.setSpacing(10)

        self._ram_prop_spin = self._add_prop_spin(res_lay, "Allocated RAM (MB)", "_ram", MIN_RAM_MB, MAX_RAM_MB, DEFAULT_RAM_MB, step=256)
        layout.addWidget(resources)

        # ===== World =====
        world = QGroupBox("World")
        world_lay = QVBoxLayout(world)
        world_lay.setSpacing(10)

        self._prop_widgets["level-seed"] = self._add_prop_entry(world_lay, "World Seed", "level-seed", "")
        display_types = [LEVEL_TYPE_NAMES.get(t, t) for t in LEVEL_TYPES]
        self._prop_widgets["level-type"] = self._add_prop_combo(world_lay, "World Type", "level-type", display_types, "Default")
        self._prop_widgets["view-distance"] = self._add_prop_spin(world_lay, "View Distance", "view-distance", 2, 32, 10)
        self._prop_widgets["simulation-distance"] = self._add_prop_spin(world_lay, "Simulation Distance", "simulation-distance", 2, 32, 10)
        self._prop_widgets["spawn-protection"] = self._add_prop_spin(world_lay, "Spawn Protection Radius", "spawn-protection", 0, 256, 16)
        self._prop_widgets["max-world-size"] = self._add_prop_spin(world_lay, "Max World Size", "max-world-size", 1000, 29999984, 29999984, step=1000)

        layout.addWidget(world)

        # ===== Network =====
        network = QGroupBox("Network")
        net_lay = QVBoxLayout(network)
        net_lay.setSpacing(10)

        self._prop_widgets["server-port"] = self._add_prop_spin(net_lay, "Server Port", "server-port", 1024, 65535, 25565)
        self._prop_widgets["online-mode"] = self._add_prop_check(net_lay, "Online Mode", "online-mode", True)
        self._prop_widgets["enable-query"] = self._add_prop_check(net_lay, "Enable Query", "enable-query", False)

        layout.addWidget(network)

        # ===== Players =====
        players = QGroupBox("Players")
        play_lay = QVBoxLayout(players)
        play_lay.setSpacing(10)

        self._prop_widgets["pvp"] = self._add_prop_check(play_lay, "PvP", "pvp", True)
        self._prop_widgets["allow-flight"] = self._add_prop_check(play_lay, "Allow Flight", "allow-flight", False)

        layout.addWidget(players)

        # ===== Advanced =====
        advanced = QGroupBox("Advanced")
        adv_lay = QVBoxLayout(advanced)
        adv_lay.setSpacing(10)

        self._prop_widgets["enable-command-block"] = self._add_prop_check(adv_lay, "Command Blocks", "enable-command-block", False)
        self._prop_widgets["allow-nether"] = self._add_prop_check(adv_lay, "Allow Nether", "allow-nether", True)
        self._prop_widgets["hardcore"] = self._add_prop_check(adv_lay, "Hardcore Mode", "hardcore", False)
        self._prop_widgets["enable-rcon"] = self._add_prop_check(adv_lay, "Enable RCON", "enable-rcon", False)

        layout.addWidget(advanced)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._tabs.addTab(tab, "Properties")

    def _add_prop_entry(self, layout, label: str, key: str, default: str) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        entry = QLineEdit(default)
        entry.setProperty("_prop_key", key)
        entry.textChanged.connect(self._schedule_save)
        row.addWidget(entry, 1)
        layout.addLayout(row)
        return entry

    def _add_prop_spin(self, layout, label: str, key: str, min_v: int, max_v: int, default: int, step: int = 1) -> QSpinBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QSpinBox()
        spin.setRange(min_v, max_v)
        spin.setSingleStep(step)
        spin.setValue(default)
        spin.setCursor(Qt.CursorShape.PointingHandCursor)
        spin.setProperty("_prop_key", key)
        spin.wheelEvent = lambda e: e.ignore()
        spin.valueChanged.connect(self._schedule_save)
        row.addWidget(spin, 1)
        layout.addLayout(row)
        return spin

    def _add_prop_combo(self, layout, label: str, key: str, options: list, default: str) -> QComboBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        combo = QComboBox()
        combo.addItems(options)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setProperty("_prop_key", key)
        combo.setProperty("_options", options)
        try:
            idx = options.index(default)
            combo.setCurrentIndex(idx)
        except ValueError:
            combo.setCurrentIndex(0)
        combo.wheelEvent = lambda e: e.ignore()
        combo.currentIndexChanged.connect(self._schedule_save)
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _add_prop_check(self, layout, label: str, key: str, default: bool) -> QCheckBox:
        check = QCheckBox(label)
        check.setCursor(Qt.CursorShape.PointingHandCursor)
        check.setChecked(default)
        check.setProperty("_prop_key", key)
        check.stateChanged.connect(self._schedule_save)
        layout.addWidget(check)
        return check

    def _load_properties(self, info: ServerInfo) -> None:
        config = self._server_manager.get_config(info.id)
        if not config:
            return

        self._prop_config = config
        self._prop_server_info = info
        config.load()
        self._populate_properties()
        self._props_banner.setVisible(False)

    def _populate_properties(self) -> None:
        if not self._prop_config:
            return

        self._suppress_prop_changes = True

        # RAM
        if hasattr(self, '_ram_prop_spin') and self._prop_server_info:
            self._ram_prop_spin.setValue(int(self._prop_server_info.ram_mb))

        for key, widget in self._prop_widgets.items():
            if isinstance(widget, QLineEdit):
                val = self._prop_config.get(key, "")
                widget.setText(val)
            elif isinstance(widget, QSpinBox):
                val = self._prop_config.get_int(key, widget.value())
                widget.setValue(val)
            elif isinstance(widget, QCheckBox):
                val = self._prop_config.get_bool(key, widget.isChecked())
                widget.setChecked(val)
            elif isinstance(widget, QComboBox):
                val = self._prop_config.get(key, "")
                options = widget.property("_options") or []
                if key == "level-type":
                    display_val = LEVEL_TYPE_NAMES.get(val, val)
                    try:
                        idx = options.index(display_val)
                        widget.setCurrentIndex(idx)
                    except (ValueError, IndexError):
                        widget.setCurrentIndex(0)
                else:
                    try:
                        idx = options.index(val)
                        widget.setCurrentIndex(idx)
                    except (ValueError, IndexError):
                        widget.setCurrentIndex(0)

        self._suppress_prop_changes = False

    def _schedule_save(self, *_args) -> None:
        if self._suppress_prop_changes:
            return
        # If the RAM spin box changed, save it directly
        if hasattr(self, '_ram_prop_spin') and self._prop_server_info:
            new_ram = self._ram_prop_spin.value()
            if self._prop_server_info.ram_mb != new_ram:
                self._server_manager.update_server_ram(self._prop_server_info.id, new_ram)
        
        self._do_auto_save()

    def _do_auto_save(self) -> None:
        if not self._prop_config:
            self._props_banner.setVisible(False)
            return

        for key, widget in self._prop_widgets.items():
            if isinstance(widget, QLineEdit):
                self._prop_config.set_value(key, widget.text())
            elif isinstance(widget, QSpinBox):
                self._prop_config.set_value(key, int(widget.value()))
            elif isinstance(widget, QCheckBox):
                self._prop_config.set_value(key, widget.isChecked())
            elif isinstance(widget, QComboBox):
                idx = widget.currentIndex()
                options = widget.property("_options") or []
                if key == "level-type":
                    display_name = options[idx] if idx < len(options) else options[0] if options else ""
                    raw_val = next(
                        (k for k, v in LEVEL_TYPE_NAMES.items() if v == display_name),
                        LEVEL_TYPES[0] if LEVEL_TYPES else "",
                    )
                    self._prop_config.set_value(key, raw_val)
                else:
                    val = options[idx] if idx < len(options) else (options[0] if options else "")
                    self._prop_config.set_value(key, val)

        self._prop_config.save()

        # Update RAM if changed
        running = False
        if self._server_manager and self._prop_server_info and hasattr(self, '_ram_prop_spin'):
            ram_mb = int(self._ram_prop_spin.value())
            if ram_mb != int(self._prop_server_info.ram_mb):
                self._server_manager.update_server_ram(self._prop_server_info.id, ram_mb)

            process = self._server_manager.get_process(self._prop_server_info.id)
            if process:
                process.set_max_players(self._prop_config.get_int("max-players", 20))
                running = bool(process.is_running)

        if self._server_manager and self._prop_server_info:
            self._server_manager.emit_on_main_thread("server-changed", self._prop_server_info.id)

        self._props_banner.setVisible(running)
