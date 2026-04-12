"""
Connect mixin — local network info and Playit.gg tunnel controls.
"""

from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hosty.backend.playit_config import load_playit_config, save_playit_config
from hosty.backend.server_manager import ServerInfo, ServerManager


PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"


class ConnectMixin:
    """Mixin providing LAN IP display and Playit.gg tunnel controls."""

    def _build_connect_tab(self) -> None:
        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Local network
        local_group = QGroupBox("Local Network")
        local_layout = QVBoxLayout(local_group)
        local_layout.setSpacing(8)

        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("LAN IP Address"))
        self._lan_ip_label = QLabel("Detecting…")
        self._lan_ip_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lan_ip_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ip_row.addWidget(self._lan_ip_label)
        local_layout.addLayout(ip_row)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Default Port"))
        port_label = QLabel("25565")
        port_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        port_label.setProperty("class", "dim")
        port_row.addWidget(port_label)
        local_layout.addLayout(port_row)

        local_info = QLabel("Players on the same network can connect using your LAN IP and port.")
        local_info.setProperty("class", "dim")
        local_info.setWordWrap(True)
        local_layout.addWidget(local_info)

        layout.addWidget(local_group)

        # Playit group
        playit_group = QGroupBox("Playit.gg Tunnel")
        playit_layout = QVBoxLayout(playit_group)
        playit_layout.setSpacing(10)

        self._playit_status_label = QLabel("Not configured")
        self._playit_status_label.setProperty("class", "dim")
        playit_layout.addWidget(self._playit_status_label)

        tunnel_row = QHBoxLayout()
        self._tunnel_btn = QPushButton("Start Tunnel")
        self._tunnel_btn.setProperty("class", "accent")
        self._tunnel_btn.clicked.connect(self._on_tunnel_toggle)
        tunnel_row.addWidget(self._tunnel_btn)
        tunnel_row.addStretch()
        playit_layout.addLayout(tunnel_row)

        self._auto_start_check = QCheckBox("Start tunnel with server")
        self._auto_start_check.setToolTip("Automatically start and stop tunnel with the server")
        self._auto_start_check.toggled.connect(self._on_auto_start_toggled)
        playit_layout.addWidget(self._auto_start_check)

        links_row = QHBoxLayout()
        dashboard_btn = QPushButton("Open Dashboard")
        dashboard_btn.clicked.connect(lambda: webbrowser.open(PLAYIT_DASHBOARD_URL))
        links_row.addWidget(dashboard_btn)

        setup_btn = QPushButton("Set Up Playit")
        setup_btn.clicked.connect(self._on_setup_playit)
        links_row.addWidget(setup_btn)
        links_row.addStretch()
        playit_layout.addLayout(links_row)

        layout.addWidget(playit_group)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._tabs.addTab(tab, "Connect")

        # State
        self._connect_server_info: Optional[ServerInfo] = None
        self._connect_cfg = {}
        self._playit_status_id = None
        self._suppress_connect_changes = False

        # Detect LAN IP
        self._detect_lan_ip()

    def _detect_lan_ip(self) -> None:
        """Detect the local IP address."""
        def worker():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
            except Exception:
                ip = "Not available"

            QTimer.singleShot(0, lambda: self._lan_ip_label.setText(ip))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_connect(self, info: ServerInfo) -> None:
        """Refresh the connect tab for the given server."""
        self._connect_server_info = info
        self._suppress_connect_changes = True

        # Load playit config
        self._connect_cfg = load_playit_config(info.server_dir)
        enabled = self._connect_cfg.get("enabled", False)
        auto_start = self._connect_cfg.get("auto_start", True)

        self._auto_start_check.setChecked(auto_start)

        # Update status
        playit = self._server_manager.playit_manager
        if self._playit_status_id is not None:
            try:
                playit.disconnect(self._playit_status_id)
            except Exception:
                pass
        self._playit_status_id = playit.connect("status-changed", self._on_playit_status_changed)
        self._refresh_playit_ui()

        self._suppress_connect_changes = False

    def _refresh_playit_ui(self) -> None:
        playit = self._server_manager.playit_manager
        if not self._connect_server_info:
            return

        sid = self._connect_server_info.id

        if playit.is_running_for(sid):
            self._playit_status_label.setText("Tunnel is running")
            self._tunnel_btn.setText("Stop Tunnel")
            self._tunnel_btn.setProperty("class", "stop")
        elif playit.is_running:
            self._playit_status_label.setText("Tunnel running for another server")
            self._tunnel_btn.setText("Start Tunnel")
            self._tunnel_btn.setProperty("class", "accent")
            self._tunnel_btn.setEnabled(False)
        else:
            self._playit_status_label.setText("Tunnel is stopped")
            self._tunnel_btn.setText("Start Tunnel")
            self._tunnel_btn.setProperty("class", "accent")
            self._tunnel_btn.setEnabled(True)

        self._tunnel_btn.style().unpolish(self._tunnel_btn)
        self._tunnel_btn.style().polish(self._tunnel_btn)

    def _on_playit_status_changed(self, *_args) -> None:
        self._refresh_playit_ui()

    def _on_tunnel_toggle(self) -> None:
        if not self._connect_server_info:
            return

        playit = self._server_manager.playit_manager
        sid = self._connect_server_info.id

        if playit.is_running_for(sid):
            playit.stop()
            return

        cfg = self._connect_cfg
        secret = str(cfg.get("secret", "")).strip()

        def worker():
            playit.start(
                sid,
                str(self._connect_server_info.server_dir),
                secret=secret,
                auto_install=True,
            )

        self._tunnel_btn.setEnabled(False)
        self._tunnel_btn.setText("Starting…")
        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_start_toggled(self, checked: bool) -> None:
        if self._suppress_connect_changes or not self._connect_server_info:
            return
        self._connect_cfg["auto_start"] = checked
        save_playit_config(self._connect_server_info.server_dir, self._connect_cfg)

    def _on_setup_playit(self) -> None:
        if not self._connect_server_info:
            QMessageBox.information(self, "Playit Setup", "Select a server first.")
            return

        try:
            from hosty.dialogs.playit_setup import PlayitSetupDialog
            # Playit setup is GTK-only; for Windows, guide user to manual setup
            QMessageBox.information(
                self,
                "Playit Setup",
                "To set up Playit.gg for this server:\n\n"
                "1. Visit https://playit.gg and create an account\n"
                "2. Download and install the Playit agent\n"
                "3. Create a tunnel for Minecraft (port 25565)\n"
                "4. The tunnel will auto-start when enabled\n\n"
                "For more info, visit the Playit documentation.",
            )
        except ImportError:
            QMessageBox.information(
                self,
                "Playit Setup",
                "To set up Playit.gg for this server:\n\n"
                "1. Visit https://playit.gg and create an account\n"
                "2. Download and install the Playit agent\n"
                "3. Create a tunnel for Minecraft (port 25565)\n"
                "4. The tunnel will auto-start when enabled",
            )
