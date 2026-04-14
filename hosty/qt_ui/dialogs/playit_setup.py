"""
Playit setup dialog for Windows UI.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QGroupBox,
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
    return False


class PlayitSetupDialog(QDialog):
    """Guided setup for playit install + claim flow."""

    def __init__(self, server_manager: ServerManager, server_info: ServerInfo, parent=None):
        super().__init__(parent)
        self._server_manager = server_manager
        self._server_info = server_info

        self._claim_url = ""
        self._setup_started = False
        self._finalize_started = False
        self._setup_completed_flag = False

        self.setWindowTitle("Set Up Playit")
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

        # Stacked pages
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
            "2. Start agent\n"
            "3. Open browser claim link to connect your account"
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

        lbl = QLabel("Claim Playit Agent")
        lbl.setProperty("class", "title")
        layout.addWidget(lbl)

        lbl2 = QLabel("Open the claim page to continue setup.")
        lbl2.setProperty("class", "subtitle")
        layout.addWidget(lbl2)

        group = QGroupBox()
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)

        self._claim_link_lbl = QLabel("Waiting for link...")
        self._claim_link_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        group_layout.addWidget(self._claim_link_lbl)

        open_btn = QPushButton("Open Claim Link")
        open_btn.setProperty("class", "accent")
        open_btn.clicked.connect(self._on_open_claim_link)
        group_layout.addWidget(open_btn)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_success_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 80, 40, 40)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        lbl = QLabel("Finish setup from the link")
        lbl.setProperty("class", "title")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        lbl2 = QLabel("Complete Playit setup in the browser link to finish connecting this server.")
        lbl2.setProperty("class", "dim")
        lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl2.setWordWrap(True)
        layout.addWidget(lbl2)
        
        return page

    def _on_action_clicked(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 0:
            self._begin_checking()
        elif idx == 3:  # Success page
            self.accept()

    def start_setup(self) -> None:
        if self._setup_started:
            return
        self._setup_started = True
        self._stack.setCurrentIndex(0)

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
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)

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

        self._update_progress(0.45, "Starting Playit agent...", "")
        start_ok, start_msg = manager.start(
            server_id,
            server_dir,
            secret="",
            auto_install=True,
            allow_unclaimed=True,
        )
        if not start_ok and not manager.is_running_for(server_id):
            self._show_error(f"Could not start Playit: {start_msg}")
            return

        self._update_progress(0.7, "Waiting for claim link...", "")
        deadline = time.monotonic() + 60.0
        claimed_since = 0.0
        while time.monotonic() < deadline:
            if manager.claim_url:
                self._claim_url = manager.claim_url
                dispatch_on_main_thread(self._show_claim_page)
                return

            if manager.has_claimed_secret() and manager.is_running_for(server_id):
                if claimed_since == 0.0:
                    claimed_since = time.monotonic()
                elif time.monotonic() - claimed_since >= 2.0:
                    self._mark_setup_complete(binary, manager)
                    return
            else:
                claimed_since = 0.0

            if manager.public_endpoint and not manager.claim_url:
                self._mark_setup_complete(binary, manager)
                return
            if not manager.is_running_for(server_id):
                self._show_error("Playit stopped while waiting for claim link.")
                return
            time.sleep(0.2)

        self._show_error("Playit did not provide a claim link in time.")

    def _run_playit_command(self, command: list[str], timeout: int) -> str:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                cwd=str(self._server_info.server_dir),
            )
            output = (result.stdout or "").strip()
            return output if result.returncode == 0 else ""
        except Exception:
            return ""

    def _show_claim_page(self) -> None:
        self._claim_link_lbl.setText(self._claim_url)
        self._stack.setCurrentIndex(2)
        self._close_btn.setEnabled(True)
        self._action_btn.setDisabled(True)
        self._action_btn.setText("Waiting for browser...")

    def _begin_finish_setup(self) -> None:
        if self._finalize_started:
            return
        self._finalize_started = True

        self._close_btn.setDisabled(True)
        self._action_btn.setDisabled(True)
        self._action_btn.setText("Finalizing...")
        self._stack.setCurrentIndex(1)
        self._update_progress(
            0.85,
            "Waiting for browser approval...",
            "In browser: Continue, set name, Add agent. Keep Playit running.",
        )

        threading.Thread(target=self._finish_setup_thread, daemon=True).start()

    def _finish_setup_thread(self) -> None:
        manager = self._server_manager.playit_manager
        server_id = self._server_info.id
        binary = manager.resolve_binary()
        if not binary:
            self._show_error("Playit binary is missing. Run setup again.")
            return

        deadline = time.monotonic() + 600.0
        while time.monotonic() < deadline:
            if manager.has_claimed_secret():
                self._mark_setup_complete(binary, manager)
                return
            if manager.public_endpoint:
                self._mark_setup_complete(binary, manager)
                return
            if manager.claim_url and manager.claim_url != self._claim_url:
                self._claim_url = manager.claim_url
            if not manager.is_running_for(server_id):
                self._show_error("Playit agent stopped before claim completed.")
                break
            time.sleep(0.25)

        if self._setup_completed_flag:
            return
        self._show_error("Timed out waiting for browser claim to complete.")

    def _mark_setup_complete(self, binary: str, manager) -> None:
        secret = self._read_secret_after_exchange(binary)

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
                self._show_error(f"Claim succeeded, but could not keep Playit running: {start_msg}")
                return

        self._setup_completed_flag = True

        dispatch_on_main_thread(self._finish_ui_state)

    def _finish_ui_state(self) -> None:
        self._stack.setCurrentIndex(3)
        self._close_btn.setDisabled(True)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Done")

    def _read_secret_after_exchange(self, binary: str) -> str:
        path_text = self._run_playit_command([binary, "--stdout", "secret-path"], timeout=8)
        if not path_text:
            return ""

        raw_path = path_text.splitlines()[-1].strip()
        if not raw_path:
            return ""

        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            return ""

        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return ""

        if not text:
            return ""

        for pattern in (
            r'(?mi)^\s*secret\s*=\s*"([^"]+)"\s*$',
            r'(?mi)^\s*secret_key\s*=\s*"([^"]+)"\s*$',
            r'(?mi)^\s*key\s*=\s*"([^"]+)"\s*$',
        ):
            m = re.search(pattern, text)
            if m:
                return m.group(1).strip()

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) == 1 and "=" not in lines[0]:
            return lines[0]

        return ""

    def _on_open_claim_link(self) -> None:
        if not self._claim_url:
            return
        if not _open_uri(self._claim_url):
            return
        self._begin_finish_setup()
