"""Preferences dialog for Hosty Windows UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from hosty.backend.preferences_manager import PreferencesManager
from hosty.utils.constants import APP_VERSION, DATA_DIR


class PreferencesDialog(QDialog):
    """Application preferences dialog."""

    def __init__(self, preferences: PreferencesManager, parent=None):
        super().__init__(parent)
        self._preferences = preferences
        self.setWindowTitle("Preferences")
        self.setMinimumSize(440, 340)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # ===== Application group =====
        app_group = QGroupBox("Application")
        app_layout = QVBoxLayout(app_group)
        app_layout.setSpacing(8)

        ver_label = QLabel(f"Version: {APP_VERSION}")
        app_layout.addWidget(ver_label)

        data_label = QLabel(f"Data folder: {DATA_DIR}")
        data_label.setWordWrap(True)
        data_label.setProperty("class", "dim")
        app_layout.addWidget(data_label)

        layout.addWidget(app_group)

        # ===== Appearance group =====
        appear_group = QGroupBox("Appearance")
        appear_layout = QVBoxLayout(appear_group)
        appear_layout.setSpacing(8)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme Mode"))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["System", "Light", "Dark"])
        
        current_theme = preferences.theme
        if current_theme == "light":
            self._theme_combo.setCurrentIndex(1)
        elif current_theme == "dark":
            self._theme_combo.setCurrentIndex(2)
        else:
            self._theme_combo.setCurrentIndex(0)

        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self._theme_combo, 1)
        appear_layout.addLayout(theme_row)
        
        layout.addWidget(appear_group)

        # ===== Behavior group =====
        behavior_group = QGroupBox("Behavior")
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.setSpacing(10)

        self._auto_backup = QCheckBox("Auto backup on stop")
        self._auto_backup.setToolTip("Create a world backup whenever a server stops")
        self._auto_backup.setChecked(preferences.auto_backup_on_stop)
        self._auto_backup.toggled.connect(self._on_auto_backup_toggled)
        behavior_layout.addWidget(self._auto_backup)

        self._auto_deps = QCheckBox("Auto resolve mod dependencies")
        self._auto_deps.setToolTip("Install required Modrinth dependencies automatically")
        self._auto_deps.setChecked(preferences.auto_resolve_mod_dependencies)
        self._auto_deps.toggled.connect(self._on_auto_deps_toggled)
        behavior_layout.addWidget(self._auto_deps)

        layout.addWidget(behavior_group)
        layout.addStretch()

    def _on_auto_backup_toggled(self, checked: bool):
        self._preferences.auto_backup_on_stop = checked

    def _on_auto_deps_toggled(self, checked: bool):
        self._preferences.auto_resolve_mod_dependencies = checked

    def _on_theme_changed(self, idx: int):
        if idx == 1:
            val = "light"
        elif idx == 2:
            val = "dark"
        else:
            val = "system"
        self._preferences.theme = val

