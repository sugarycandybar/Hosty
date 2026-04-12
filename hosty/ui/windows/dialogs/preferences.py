"""Preferences dialog for Hosty Windows UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
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
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

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
