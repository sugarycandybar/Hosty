"""
Playit setup dialog for Windows UI.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.core.events import dispatch_on_main_thread


def _open_uri(uri: str) -> bool:
    try:
        if webbrowser.open(uri):
            return True
    except Exception:
        pass

    try:
        cmd = ["open", uri] if sys.platform == "darwin" else ["xdg-open", uri]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        pass

    return False


class PlayitSetupDialog(QDialog):
    """Guided setup for playit install + setup-code link flow."""

    def __init__(self, server_manager: ServerManager, server_info: ServerInfo, parent=None):
        super().__init__(parent)
        self._server_manager = server_manager
        self._server_info = server_info

        self._claim_url = ""
        self._setup_started = False
        self._finalize_started = False
        self._setup_completed_flag = False
        self._did_try_open_setup_link = False

        self.setWindowTitle("Set Up Playit")
        self.setMinimumSize(520, 380)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setProperty("class", "header-bar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)

        title = QLabel("Playit.gg Setup")
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

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._stack.addWidget(self._build_steps_page())
        self._stack.addWidget(self._build_progress_page())
        self._stack.addWidget(self._build_claim_page())
        self._stack.addWidget(self._build_success_page())

    def _build_steps_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        lbl = QLabel("Playit Installation Steps")
        lbl.setProperty("class", "title")
        layout.addWidget(lbl)

        steps_text = (
            "1. Download Playit agent\n"
            "2. Open the Hosty setup URL\n"
            "3. Paste your setup code\n"
            "4. Link account and start tunnel"
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

        self._progress_title = QLabel("Preparing Playit")
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

    def _build_claim_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        lbl = QLabel("Link Playit Account")
        lbl.setProperty("class", "title")
        layout.addWidget(lbl)

        lbl2 = QLabel("Open the setup link, copy the setup code, and paste it below.")
        lbl2.setProperty("class", "subtitle")
        lbl2.setWordWrap(True)
        layout.addWidget(lbl2)

        group = QGroupBox()
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)

        self._claim_link_lbl = QLabel("Waiting for setup link...")
        self._claim_link_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._claim_link_lbl.setWordWrap(True)
        group_layout.addWidget(self._claim_link_lbl)

        open_btn = QPushButton("Open Setup Link")
        open_btn.setProperty("class", "accent")
        open_btn.clicked.connect(self._on_open_claim_link)
        group_layout.addWidget(open_btn)

        self._setup_code_input = QLineEdit()
        self._setup_code_input.setPlaceholderText("Paste setup code")
        group_layout.addWidget(self._setup_code_input)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_success_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 80, 40, 40)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        lbl = QLabel("Playit setup complete")
        lbl.setProperty("class", "title")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        lbl2 = QLabel("Your account is linked and tunnel startup is ready for this server.")
        lbl2.setProperty("class", "dim")
        lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl2.setWordWrap(True)
        layout.addWidget(lbl2)

        return page

    def _on_action_clicked(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 0:
            self._begin_checking()
        elif idx == 2:
            self._begin_finish_setup()
        elif idx == 3:
            self.accept()

    def start_setup(self) -> None:
        if self._setup_started:
            return
        self._setup_started = True
        self._begin_checking()

    def _begin_checking(self) -> None:
        self._action_btn.setDisabled(True)
        self._close_btn.setDisabled(True)
        self._stack.setCurrentIndex(1)
        threading.Thread(target=self._setup_thread, daemon=True).start()

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

        dispatch_on_main_thread(ui)

    def _setup_thread(self) -> None:
        manager = self._server_manager.playit_manager

        self._update_progress(0.2, "Checking Playit installation...", "")
        binary = manager.resolve_binary()
        if not binary:
            ok, msg = manager.install_latest_binary()
            if not ok:
                self._show_error(f"Could not install Playit: {msg}")
                return
            binary = manager.resolve_binary()

        if not binary:
            self._show_error("Playit install completed but binary path was not found.")
            return

        if manager.has_claimed_secret():
            linked_ok, _detail = manager.validate_existing_link(retry_attempts=3)
            if linked_ok:
                self._mark_setup_complete(manager)
                return

        self._claim_url = manager.setup_url
        dispatch_on_main_thread(self._show_claim_page)

    def _show_claim_page(self) -> None:
        self._claim_link_lbl.setText(self._claim_url)
        self._stack.setCurrentIndex(2)
        self._close_btn.setEnabled(True)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Link Code")
        if self._claim_url and not self._did_try_open_setup_link:
            self._did_try_open_setup_link = True
            _open_uri(self._claim_url)

    def _begin_finish_setup(self) -> None:
        if self._finalize_started:
            return

        setup_code = self._setup_code_input.text().strip()
        if not setup_code:
            self._show_error("Paste the setup code from the browser first.")
            return

        self._finalize_started = True
        self._close_btn.setDisabled(True)
        self._action_btn.setDisabled(True)
        self._action_btn.setText("Linking...")
        self._stack.setCurrentIndex(1)
        self._update_progress(0.85, "Linking account...", "Exchanging setup code with playit.")

        threading.Thread(target=self._finish_setup_thread, args=(setup_code,), daemon=True).start()

    def _finish_setup_thread(self, setup_code: str) -> None:
        manager = self._server_manager.playit_manager
        ok, msg = manager.link_account(setup_code)
        if not ok:
            self._show_error(f"Could not link playit account: {msg}")
            return

        self._mark_setup_complete(manager)

    def _mark_setup_complete(self, manager) -> None:
        secret = manager.read_claimed_secret()

        cfg = load_playit_config(self._server_info.server_dir)
        cfg["enabled"] = True
        cfg["setup_complete"] = True
        cfg["auto_start"] = bool(cfg.get("auto_start", True))
        cfg["auto_install"] = True
        if secret:
            cfg["secret"] = secret
        save_playit_config(self._server_info.server_dir, cfg)

        if not manager.is_running_for(self._server_info.id):
            start_ok, start_msg = manager.start(
                self._server_info.id,
                str(self._server_info.server_dir),
                secret=str(cfg.get("secret", "")).strip(),
                auto_install=True,
                allow_unclaimed=False,
            )
            if not start_ok:
                self._show_error(f"Account linked, but could not start Playit: {start_msg}")
                return

        self._setup_completed_flag = True
        dispatch_on_main_thread(self._finish_ui_state)

    def _finish_ui_state(self) -> None:
        self._stack.setCurrentIndex(3)
        self._close_btn.setDisabled(True)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Done")

    def _on_open_claim_link(self) -> None:
        if not self._claim_url:
            return
        _open_uri(self._claim_url)
