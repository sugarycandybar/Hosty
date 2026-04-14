"""
Connect mixin — local network info, Playit.gg tunnel controls,
and player management (whitelist + ban).

Matches the Linux ConnectView feature set.
"""

from __future__ import annotations

import json
import socket
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
import urllib.parse
import urllib.request

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)
from ..components import SmoothScrollArea

from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.core.events import dispatch_on_main_thread


PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"


class ConnectMixin:
    """Mixin providing LAN IP display, Playit.gg tunnel controls, and player management."""

    def _build_connect_tab(self) -> None:
        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # ===== Local network group =====
        local_group = QGroupBox("Local Network")
        local_layout = QVBoxLayout(local_group)
        local_layout.setSpacing(8)

        ip_row = QHBoxLayout()
        ip_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        ip_row.addWidget(QLabel("LAN IP Address"))
        
        ip_row.addStretch(1)

        self._lan_ip_label = QLabel("Detecting…")
        self._lan_ip_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ip_row.addWidget(self._lan_ip_label)

        from ..theme import get_material_icon, get_colors, is_system_dark
        icon_color = get_colors(is_system_dark()).get("accent", "#7c6bf0")

        copy_ip_btn = QPushButton()
        copy_ip_btn.setIcon(get_material_icon("content_copy", icon_color, 16))
        copy_ip_btn.setFixedSize(28, 28)
        copy_ip_btn.setProperty("class", "flat")
        copy_ip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_ip_btn.setToolTip("Copy LAN IP")
        copy_ip_btn.clicked.connect(self._copy_lan_ip)
        ip_row.addWidget(copy_ip_btn)

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

        # ===== Zrok group =====
        zrok_group = QGroupBox("Zrok Tunnel (Low-lag option)")
        zrok_layout = QVBoxLayout(zrok_group)
        zrok_layout.setSpacing(10)

        self._zrok_status_label = QLabel("Not configured")
        self._zrok_status_label.setProperty("class", "dim")
        zrok_layout.addWidget(self._zrok_status_label)

        zrok_tunnel_row = QHBoxLayout()
        self._zrok_tunnel_btn = QPushButton("Start Zrok")
        self._zrok_tunnel_btn.setProperty("class", "accent")
        self._zrok_tunnel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zrok_tunnel_btn.clicked.connect(self._on_zrok_tunnel_toggle)
        zrok_tunnel_row.addWidget(self._zrok_tunnel_btn)
        zrok_tunnel_row.addStretch()
        zrok_layout.addLayout(zrok_tunnel_row)

        self._zrok_auto_start_check = QCheckBox("Start tunnel with server")
        self._zrok_auto_start_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zrok_auto_start_check.toggled.connect(self._on_zrok_auto_start_toggled)
        zrok_layout.addWidget(self._zrok_auto_start_check)

        zrok_links_row = QHBoxLayout()
        zrok_setup_btn = QPushButton("Set Up Zrok")
        zrok_setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        zrok_setup_btn.clicked.connect(self._on_setup_zrok)
        zrok_links_row.addWidget(zrok_setup_btn)
        zrok_links_row.addStretch()
        zrok_layout.addLayout(zrok_links_row)

        layout.addWidget(zrok_group)

        # ===== Playit group =====
        playit_group = QGroupBox("Playit.gg Tunnel")
        playit_layout = QVBoxLayout(playit_group)
        playit_layout.setSpacing(10)

        self._playit_status_label = QLabel("Not configured")
        self._playit_status_label.setProperty("class", "dim")
        playit_layout.addWidget(self._playit_status_label)

        tunnel_row = QHBoxLayout()
        self._tunnel_btn = QPushButton("Start Playit")
        self._tunnel_btn.setProperty("class", "accent")
        self._tunnel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tunnel_btn.clicked.connect(self._on_tunnel_toggle)
        tunnel_row.addWidget(self._tunnel_btn)
        tunnel_row.addStretch()
        playit_layout.addLayout(tunnel_row)

        self._auto_start_check = QCheckBox("Start tunnel with server")
        self._auto_start_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_start_check.setToolTip("Automatically start and stop tunnel with the server")
        self._auto_start_check.toggled.connect(self._on_auto_start_toggled)
        playit_layout.addWidget(self._auto_start_check)

        links_row = QHBoxLayout()
        dashboard_btn = QPushButton("Open Dashboard")
        dashboard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dashboard_btn.clicked.connect(lambda: webbrowser.open(PLAYIT_DASHBOARD_URL))
        links_row.addWidget(dashboard_btn)

        setup_btn = QPushButton("Set Up Playit")
        setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        setup_btn.clicked.connect(self._on_setup_playit)
        links_row.addWidget(setup_btn)
        links_row.addStretch()
        playit_layout.addLayout(links_row)

        layout.addWidget(playit_group)

        # ===== Players group =====
        players_group = QGroupBox("Players")
        players_layout = QVBoxLayout(players_group)
        players_layout.setSpacing(10)

        # Player name input
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self._player_name_input = QLineEdit()
        self._player_name_input.setPlaceholderText("Player name…")
        name_row.addWidget(self._player_name_input, 1)

        add_whitelist_btn = QPushButton("Add to Whitelist")
        add_whitelist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_whitelist_btn.setProperty("class", "accent")
        add_whitelist_btn.clicked.connect(self._on_add_whitelist)
        name_row.addWidget(add_whitelist_btn)

        ban_btn = QPushButton("Ban")
        ban_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ban_btn.setProperty("class", "destructive")
        ban_btn.clicked.connect(self._on_ban_player)
        name_row.addWidget(ban_btn)

        players_layout.addLayout(name_row)

        self._player_status_label = QLabel("")
        self._player_status_label.setProperty("class", "dim")
        players_layout.addWidget(self._player_status_label)

        layout.addWidget(players_group)

        # ===== Whitelist group =====
        self._whitelist_enabled_check = QCheckBox("Whitelist enabled (only whitelisted players can join)")
        self._whitelist_enabled_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._whitelist_enabled_check.toggled.connect(self._on_whitelist_toggled)
        self._suppress_whitelist_toggle = False

        whitelist_group = QGroupBox("Whitelist")
        whitelist_layout = QVBoxLayout(whitelist_group)
        whitelist_layout.setSpacing(8)
        whitelist_layout.addWidget(self._whitelist_enabled_check)

        self._whitelist_container = QVBoxLayout()
        self._whitelist_container.setSpacing(6)
        whitelist_layout.addLayout(self._whitelist_container)

        layout.addWidget(whitelist_group)

        # ===== Banned group =====
        banned_group = QGroupBox("Banned Players")
        banned_layout = QVBoxLayout(banned_group)
        banned_layout.setSpacing(8)

        self._banned_container = QVBoxLayout()
        self._banned_container.setSpacing(6)
        banned_layout.addLayout(self._banned_container)

        layout.addWidget(banned_group)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._content_stack.addWidget(tab)

        # State
        self._connect_server_info: Optional[ServerInfo] = None
        self._connect_cfg = {}
        self._zrok_cfg = {}
        self._playit_status_id = None
        self._zrok_status_id = None
        self._suppress_connect_changes = False

        # Detect LAN IP
        self._detect_lan_ip()

    # ===== LAN IP =====

    def _detect_lan_ip(self) -> None:
        def worker():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
            except Exception:
                ip = "Not available"
            dispatch_on_main_thread(lambda: self._lan_ip_label.setText(ip))

        threading.Thread(target=worker, daemon=True).start()

    def _copy_lan_ip(self) -> None:
        ip = self._lan_ip_label.text().strip()
        if not ip or ip in ("Detecting…", "Not available"):
            return
        try:
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(ip)
                self._player_status_label.setText("✓ IP copied to clipboard")
                QTimer.singleShot(3000, lambda: (
                    self._player_status_label.setText("") if "copied" in self._player_status_label.text() else None
                ))
        except Exception:
            pass

    # ===== Playit =====

    def _is_setup_complete(self) -> bool:
        if not self._server_manager:
            return False
        return bool(
            self._connect_cfg.get("enabled", False)
            and self._connect_cfg.get("setup_complete", False)
            and (
                self._server_manager.playit_manager.has_claimed_secret()
                or bool(str(self._connect_cfg.get("secret", "")).strip())
            )
        )

    def _refresh_connect(self, info: ServerInfo) -> None:
        from hosty.shared.backend.zrok_config import load_zrok_config
        self._connect_server_info = info
        self._suppress_connect_changes = True

        self._connect_cfg = load_playit_config(info.server_dir)
        auto_start = self._connect_cfg.get("auto_start", True)
        self._auto_start_check.setChecked(auto_start)

        self._zrok_cfg = load_zrok_config(info.server_dir)
        z_auto_start = self._zrok_cfg.get("auto_start", True)
        self._zrok_auto_start_check.setChecked(z_auto_start)

        playit = self._server_manager.playit_manager
        if self._playit_status_id is not None:
            try:
                playit.disconnect(self._playit_status_id)
            except Exception:
                pass
        self._playit_status_id = playit.connect("status-changed", self._on_playit_status_changed)

        zrok = self._server_manager.zrok_manager
        if self._zrok_status_id is not None:
            try:
                zrok.disconnect(self._zrok_status_id)
            except Exception:
                pass
        self._zrok_status_id = zrok.connect("status-changed", self._on_zrok_status_changed)

        self._refresh_playit_ui()
        self._refresh_zrok_ui()

        self._suppress_connect_changes = False

        # Refresh player lists
        self._refresh_whitelist_status()
        self._refresh_player_lists()

    def _refresh_playit_ui(self) -> None:
        playit = self._server_manager.playit_manager
        if not self._connect_server_info:
            return

        sid = self._connect_server_info.id

        if playit.is_running_for(sid):
            self._playit_status_label.setText("Tunnel is running")
            self._tunnel_btn.setText("Stop Tunnel")
            self._tunnel_btn.setProperty("class", "stop")
            self._tunnel_btn.setEnabled(True)
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
        if not self._is_setup_complete():
            self._on_setup_playit()
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
            return

        from hosty.qt_ui.dialogs.playit_setup import PlayitSetupDialog
        dialog = PlayitSetupDialog(
            self._server_manager,
            self._connect_server_info,
            self
        )
        dialog.start_setup()
        dialog.exec()

        if dialog.setup_completed():
            self._refresh_connect(self._connect_server_info)

    # ===== Zrok =====

    def _is_zrok_setup_complete(self) -> bool:
        if not self._server_manager:
            return False
        return bool(
            self._zrok_cfg.get("enabled", False)
            and self._zrok_cfg.get("setup_complete", False)
            and (
                self._server_manager.zrok_manager.check_enabled()
                or bool(str(self._zrok_cfg.get("token", "")).strip())
            )
        )

    def _refresh_zrok_ui(self) -> None:
        zrok = self._server_manager.zrok_manager
        if not self._connect_server_info:
            return

        sid = self._connect_server_info.id

        if zrok.is_running_for(sid):
            self._zrok_status_label.setText("Tunnel is running")
            self._zrok_tunnel_btn.setText("Stop Zrok")
            self._zrok_tunnel_btn.setProperty("class", "stop")
            self._zrok_tunnel_btn.setEnabled(True)
        elif zrok.is_running:
            self._zrok_status_label.setText("Tunnel running for another server")
            self._zrok_tunnel_btn.setText("Start Zrok")
            self._zrok_tunnel_btn.setProperty("class", "accent")
            self._zrok_tunnel_btn.setEnabled(False)
        else:
            self._zrok_status_label.setText("Tunnel is stopped")
            self._zrok_tunnel_btn.setText("Start Zrok")
            self._zrok_tunnel_btn.setProperty("class", "accent")
            self._zrok_tunnel_btn.setEnabled(True)

        self._zrok_tunnel_btn.style().unpolish(self._zrok_tunnel_btn)
        self._zrok_tunnel_btn.style().polish(self._zrok_tunnel_btn)

    def _on_zrok_status_changed(self, *_args) -> None:
        self._refresh_zrok_ui()

    def _on_zrok_tunnel_toggle(self) -> None:
        if not self._connect_server_info:
            return
        if not self._is_zrok_setup_complete():
            self._on_setup_zrok()
            return

        zrok = self._server_manager.zrok_manager
        sid = self._connect_server_info.id

        if zrok.is_running_for(sid):
            zrok.stop()
            return

        def worker():
            config = self._server_manager.get_config(sid)
            port = 25565
            if config:
                config.load()
                port = config.get_int("server-port", 25565)
            zrok.start(
                sid,
                str(self._connect_server_info.server_dir),
                port=port,
                auto_install=True,
            )

        self._zrok_tunnel_btn.setEnabled(False)
        self._zrok_tunnel_btn.setText("Starting…")
        threading.Thread(target=worker, daemon=True).start()

    def _on_zrok_auto_start_toggled(self, checked: bool) -> None:
        from hosty.shared.backend.zrok_config import save_zrok_config
        if self._suppress_connect_changes or not self._connect_server_info:
            return
        self._zrok_cfg["auto_start"] = checked
        save_zrok_config(self._connect_server_info.server_dir, self._zrok_cfg)

    def _on_setup_zrok(self) -> None:
        if not self._connect_server_info:
            return

        from hosty.qt_ui.dialogs.zrok_setup import ZrokSetupDialog
        dialog = ZrokSetupDialog(
            self._server_manager,
            self._connect_server_info,
            self
        )
        dialog.start_setup()
        dialog.exec()

        if dialog.setup_completed():
            self._refresh_connect(self._connect_server_info)

    # ===== Player Management =====

    def _player_list_paths(self):
        if not self._connect_server_info:
            return None, None
        root = Path(self._connect_server_info.server_dir)
        return root / "whitelist.json", root / "banned-players.json"

    def _read_player_list(self, path: Optional[Path]) -> list:
        if not path or not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            out.append(item)
        return sorted(out, key=lambda e: str(e.get("name", "")).lower())

    def _write_player_list(self, path: Optional[Path], entries: list) -> bool:
        if not path:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
            return True
        except Exception:
            return False

    def _refresh_whitelist_status(self) -> None:
        enabled = False
        if self._server_manager and self._connect_server_info:
            cfg = self._server_manager.get_config(self._connect_server_info.id)
            if cfg:
                cfg.load()
                enabled = cfg.get_bool("white-list", False)

        self._suppress_whitelist_toggle = True
        self._whitelist_enabled_check.setChecked(enabled)
        self._suppress_whitelist_toggle = False

    def _on_whitelist_toggled(self, checked: bool) -> None:
        if self._suppress_whitelist_toggle:
            return
        if not self._server_manager or not self._connect_server_info:
            return

        cfg = self._server_manager.get_config(self._connect_server_info.id)
        if cfg:
            cfg.load()
            cfg.set_value("white-list", checked)
            cfg.save()

        process = self._server_manager.get_process(self._connect_server_info.id)
        if process and process.is_running:
            self._player_status_label.setText("⚠ Restart the server to apply whitelist changes")

    def _refresh_player_lists(self) -> None:
        # Clear existing
        self._clear_layout(self._whitelist_container)
        self._clear_layout(self._banned_container)

        whitelist_path, banned_path = self._player_list_paths()
        if not whitelist_path or not banned_path:
            return

        whitelist = self._read_player_list(whitelist_path)
        banned = self._read_player_list(banned_path)

        if not whitelist:
            lbl = QLabel("No whitelisted players")
            lbl.setProperty("class", "dim")
            self._whitelist_container.addWidget(lbl)
        else:
            for entry in whitelist:
                name = str(entry.get("name", "")).strip()
                uuid_str = str(entry.get("uuid", "")).strip()
                row = self._build_player_row(name, uuid_str, is_whitelist=True)
                self._whitelist_container.addWidget(row)

        if not banned:
            lbl = QLabel("No banned players")
            lbl.setProperty("class", "dim")
            self._banned_container.addWidget(lbl)
        else:
            for entry in banned:
                name = str(entry.get("name", "")).strip()
                reason = str(entry.get("reason", "Banned")).strip()
                row = self._build_player_row(name, reason, is_whitelist=False)
                self._banned_container.addWidget(row)

    def _build_player_row(self, name: str, subtitle: str, is_whitelist: bool) -> QWidget:
        row = QWidget()
        row.setProperty("class", "card")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-weight: 700;")
        layout.addWidget(name_lbl, 1)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setProperty("class", "dim")
        layout.addWidget(sub_lbl)

        remove_btn = QPushButton("Remove" if is_whitelist else "Pardon")
        remove_btn.setProperty("class", "flat")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if is_whitelist:
            remove_btn.clicked.connect(lambda *_, n=name: self._remove_whitelist_player(n))
        else:
            remove_btn.clicked.connect(lambda *_, n=name: self._remove_banned_player(n))
        layout.addWidget(remove_btn)

        return row

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _resolve_profile(self, name: str):
        try:
            quoted = urllib.parse.quote(name)
            req = urllib.request.Request(
                f"https://api.mojang.com/users/profiles/minecraft/{quoted}",
                headers={"User-Agent": "Hosty/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if resp.status == 204:
                    return name, ""
                data = json.loads(resp.read().decode("utf-8"))
            resolved_name = str(data.get("name", name)).strip() or name
            raw_uuid = str(data.get("id", "")).strip()
            if len(raw_uuid) == 32:
                resolved_uuid = f"{raw_uuid[0:8]}-{raw_uuid[8:12]}-{raw_uuid[12:16]}-{raw_uuid[16:20]}-{raw_uuid[20:32]}"
            else:
                resolved_uuid = raw_uuid
            return resolved_name, resolved_uuid
        except Exception:
            return name, ""

    def _on_add_whitelist(self) -> None:
        name = self._player_name_input.text().strip()
        if not name:
            self._player_status_label.setText("⚠ Enter a player name first.")
            return
        self._add_player("whitelist", name)

    def _on_ban_player(self) -> None:
        name = self._player_name_input.text().strip()
        if not name:
            self._player_status_label.setText("⚠ Enter a player name first.")
            return
        self._add_player("banned", name)

    def _add_player(self, list_type: str, name: str) -> None:
        whitelist_path, banned_path = self._player_list_paths()
        path = whitelist_path if list_type == "whitelist" else banned_path
        if not path:
            return

        self._player_status_label.setText(f"Resolving {name}…")

        # Send command to running server if applicable
        process = None
        if self._server_manager and self._connect_server_info:
            process = self._server_manager.get_process(self._connect_server_info.id)
        if process and process.is_running:
            if list_type == "whitelist":
                process.send_command(f"whitelist add {name}")
            else:
                process.send_command(f"ban {name}")

        def worker():
            resolved_name, resolved_uuid = self._resolve_profile(name)

            def ui_apply():
                entries = self._read_player_list(path)
                if any(str(e.get("name", "")).lower() == resolved_name.lower() for e in entries):
                    self._player_status_label.setText(f"{resolved_name} is already listed")
                    return

                if list_type == "whitelist":
                    entries.append({"uuid": resolved_uuid, "name": resolved_name})
                    saved = self._write_player_list(path, entries)
                    if saved:
                        self._player_status_label.setText(f"✓ Added {resolved_name} to whitelist")
                else:
                    entries.append({
                        "uuid": resolved_uuid,
                        "name": resolved_name,
                        "created": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S +0000"),
                        "source": "Hosty",
                        "expires": "forever",
                        "reason": "Banned by Hosty",
                    })
                    saved = self._write_player_list(path, entries)
                    if saved:
                        self._player_status_label.setText(f"✓ Banned {resolved_name}")

                if saved:
                    self._refresh_player_lists()
                    self._player_name_input.clear()
                else:
                    self._player_status_label.setText("✗ Failed to save player list")

            dispatch_on_main_thread(ui_apply)

        threading.Thread(target=worker, daemon=True).start()

    def _remove_whitelist_player(self, name: str) -> None:
        whitelist_path, _ = self._player_list_paths()
        if not whitelist_path:
            return

        entries = self._read_player_list(whitelist_path)
        new_entries = [e for e in entries if str(e.get("name", "")).lower() != name.lower()]
        if len(new_entries) == len(entries):
            return

        if self._write_player_list(whitelist_path, new_entries):
            process = None
            if self._server_manager and self._connect_server_info:
                process = self._server_manager.get_process(self._connect_server_info.id)
            if process and process.is_running:
                process.send_command(f"whitelist remove {name}")
            self._refresh_player_lists()
            self._player_status_label.setText(f"✓ Removed {name} from whitelist")

    def _remove_banned_player(self, name: str) -> None:
        _, banned_path = self._player_list_paths()
        if not banned_path:
            return

        entries = self._read_player_list(banned_path)
        new_entries = [e for e in entries if str(e.get("name", "")).lower() != name.lower()]
        if len(new_entries) == len(entries):
            return

        if self._write_player_list(banned_path, new_entries):
            process = None
            if self._server_manager and self._connect_server_info:
                process = self._server_manager.get_process(self._connect_server_info.id)
            if process and process.is_running:
                process.send_command(f"pardon {name}")
            self._refresh_player_lists()
            self._player_status_label.setText(f"✓ Pardoned {name}")
