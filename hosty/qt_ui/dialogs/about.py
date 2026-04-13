"""About dialog for Hosty Windows UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from hosty.shared.utils.constants import APP_NAME, APP_VERSION, APP_WEBSITE


class AboutDialog(QDialog):
    """Simple about dialog showing app info."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(400, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(32, 32, 32, 24)

        # App icon placeholder (text-based)
        icon_label = QLabel("🎮")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 48px;")
        layout.addWidget(icon_label)

        name_label = QLabel(APP_NAME)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(name_label)

        ver_label = QLabel(f"Version {APP_VERSION}")
        ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_label.setProperty("class", "subtitle")
        layout.addWidget(ver_label)

        desc_label = QLabel(
            "A modern application for creating, running,\n"
            "and managing Fabric Minecraft servers with ease."
        )
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addSpacing(8)

        website_btn = QPushButton("Visit Website")
        website_btn.setProperty("class", "accent")
        website_btn.clicked.connect(self._open_website)
        layout.addWidget(website_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        license_label = QLabel("Licensed under GPL-3.0")
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        license_label.setProperty("class", "dim")
        layout.addWidget(license_label)

        layout.addStretch()

    def _open_website(self):
        import webbrowser
        webbrowser.open(APP_WEBSITE)
