"""
Zrok setup dialog for Windows(Qt) UI.
"""

from __future__ import annotations

import threading
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QGroupBox,
)

from hosty.shared.backend.zrok_config import load_zrok_config, save_zrok_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.core.events import dispatch_on_main_thread


def _open_uri(uri: str) -> bool:
    try:
        if webbrowser.open(uri):
            return True
    except Exception:
        pass
    return False


class ZrokSetupDialog(QDialog):
    """Guided setup for zrok install + claim flow."""

    def __init__(self, server_manager: ServerManager, server_info: ServerInfo, parent=None):
        super().__init__(parent)
        self._server_manager = server_manager
        self._server_info = server_info

        self._setup_started = False
        self._setup_completed_flag = False

        self.setWindowTitle("Set Up Zrok")
        self.setMinimumSize(500, 360)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QWidget()
        header.setProperty("class", "header-bar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)

        title = QLabel("Zrok Setup")
        title.setProperty("class", "title")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self._close_btn = QPushButton("Cancel")
        self._close_btn.setProperty("class", "flat")
        self._close_btn.clicked.connect(self.reject)
        header_layout.addWidget(self._close_btn)

        self._action_btn = QPushButton("Next")
        self._action_btn.setProperty("class", "accent")
        self._action_btn.clicked.connect(self._on_action_clicked)
        header_layout.addWidget(self._action_btn)

        root.addWidget(header)

        # Stacked pages
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._stack.addWidget(self._build_steps_page())
        self._stack.addWidget(self._build_progress_page())
        self._stack.addWidget(self._build_token_page())
        self._stack.addWidget(self._build_success_page())

    def _build_steps_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        lbl = QLabel("Zrok Installation Steps")
        lbl.setProperty("class", "title")
        layout.addWidget(lbl)

        steps_text = (
            "1. Download Zrok core\n"
            "2. Log in with your Zrok account\n"
            "3. Start tunnel"
        )
        steps_lbl = QLabel(steps_text)
        steps_lbl.setProperty("class", "subtitle")
        layout.addWidget(steps_lbl)

        layout.addStretch()
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)
        
        self._progress_title = QLabel("Preparing Zrok")
        self._progress_title.setProperty("class", "title")
        layout.addWidget(self._progress_title)

        self._progress_desc = QLabel("Starting setup...")
        self._progress_desc.setProperty("class", "subtitle")
        layout.addWidget(self._progress_desc)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        self._progress_detail = QLabel("")
        self._progress_detail.setProperty("class", "dim")
        self._progress_detail.setWordWrap(True)
        layout.addWidget(self._progress_detail)

        layout.addStretch()
        return page

    def _build_token_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        lbl = QLabel("Connect Zrok Account")
        lbl.setProperty("class", "title")
        layout.addWidget(lbl)

        lbl2 = QLabel("Enter your Zrok email and password. A token will be retrieved automatically. If you don't have an account, visit zrok.io.")
        lbl2.setProperty("class", "subtitle")
        lbl2.setWordWrap(True)
        layout.addWidget(lbl2)

        self._email_input = QLineEdit()
        self._email_input.setPlaceholderText("Email address")
        layout.addWidget(self._email_input)

        self._password_input = QLineEdit()
        self._password_input.setPlaceholderText("Password")
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._password_input)

        layout.addStretch()
        return page

    def _build_success_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 80, 40, 40)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        lbl = QLabel("Zrok Setup Complete")
        lbl.setProperty("class", "title")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        lbl2 = QLabel("Your Zrok tunnel is ready to be started.")
        lbl2.setProperty("class", "dim")
        lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl2.setWordWrap(True)
        layout.addWidget(lbl2)
        
        return page

    def _on_action_clicked(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 0:  # Intial Steps -> run installer or prompt token
            self._begin_checking()
        elif idx == 2: # Token page -> verify token
            self._apply_token()
        elif idx == 3:  # Success page
            self.accept()

    def start_setup(self) -> None:
        if self._setup_started:
            return
        self._setup_started = True
        self._stack.setCurrentIndex(0)

    def setup_completed(self) -> bool:
        return self._setup_completed_flag

    def _update_progress(self, fraction: float, title: str, detail: str) -> None:
        def ui():
            self._progress_bar.setValue(int(max(0.0, min(1.0, fraction)) * 100))
            self._progress_title.setText(title)
            self._progress_desc.setText(detail)

        dispatch_on_main_thread(ui)

    def _show_error(self, message: str) -> None:
        def ui():
            self._progress_title.setText("Setup failed")
            self._progress_desc.setText(message)
            self._progress_bar.setValue(0)
            self._progress_detail.setText("You can close this dialog and try again.")
            self._close_btn.setEnabled(True)
            self._action_btn.setDisabled(True)
            self._action_btn.setText("Failed")
            self._stack.setCurrentIndex(1) # progress page shows error

        dispatch_on_main_thread(ui)

    def _begin_checking(self) -> None:
        self._close_btn.setDisabled(True)
        self._action_btn.setDisabled(True)
        self._stack.setCurrentIndex(1) # Progress
        threading.Thread(target=self._checking_thread, daemon=True).start()

    def _checking_thread(self) -> None:
        manager = self._server_manager.zrok_manager
        self._update_progress(0.2, "Checking Zrok installation...", "")
        binary = manager.resolve_binary()
        if not binary:
            ok, msg = manager.install_latest_binary()
            if not ok:
                self._show_error(f"Could not install Zrok: {msg}")
                return

        manager = self._server_manager.zrok_manager
        if manager.check_enabled():
            # Already enabled
            self._mark_setup_complete()
            return
            
        def ui():
            self._stack.setCurrentIndex(2)
            self._close_btn.setEnabled(True)
            self._action_btn.setEnabled(True)
            self._action_btn.setText("Enable")
        dispatch_on_main_thread(ui)

    def _apply_token(self) -> None:
        email = self._email_input.text().strip()
        password = self._password_input.text()
        if not email or not password:
            return

        self._close_btn.setDisabled(True)
        self._action_btn.setDisabled(True)
        self._action_btn.setText("Authenticating...")
        self._stack.setCurrentIndex(1)

        def worker():
            import json, urllib.request, urllib.error
            manager = self._server_manager.zrok_manager
            self._update_progress(0.4, "Connecting to Zrok...", "")

            try:
                data = json.dumps({"email": email, "password": password}).encode("utf-8")
                req = urllib.request.Request("https://api.zrok.io/api/v1/login", data=data)
                req.add_header("Content-Type", "application/zrok.v1+json")
                with urllib.request.urlopen(req) as response:
                    resp_data = json.loads(response.read().decode("utf-8"))
                    token = resp_data.get("token")
                    if not token:
                        raise Exception("No token received in response")
            except urllib.error.HTTPError as e:
                self._show_error(f"Authentication failed: {e.code} {e.reason}")
                return
            except Exception as e:
                self._show_error(f"Authentication failed: {str(e)}")
                return

            self._update_progress(0.8, "Enabling token...", "")
            ok, msg = manager.enable(token)
            if not ok:
                self._show_error(f"Failed to enable token: {msg}")
                return
            self._mark_setup_complete(token=token)

        threading.Thread(target=worker, daemon=True).start()

    def _mark_setup_complete(self, token: str = "") -> None:
        cfg = load_zrok_config(self._server_info.server_dir)
        cfg["enabled"] = True
        cfg["setup_complete"] = True
        cfg["auto_start"] = bool(cfg.get("auto_start", True))
        cfg["auto_install"] = True
        if token:
            cfg["token"] = token
        save_zrok_config(self._server_info.server_dir, cfg)

        self._setup_completed_flag = True

        dispatch_on_main_thread(self._finish_ui_state)

    def _finish_ui_state(self) -> None:
        self._stack.setCurrentIndex(3)
        self._close_btn.setDisabled(True)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Done")
