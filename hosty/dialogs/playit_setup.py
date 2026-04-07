"""
Playit setup dialog.
"""
from __future__ import annotations

import threading
import subprocess
import sys
import webbrowser
import re
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk, Gdk

from hosty.backend.playit_config import load_playit_config, save_playit_config
from hosty.backend.server_manager import ServerInfo, ServerManager


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
        return False


class PlayitSetupDialog(Adw.Dialog):
    """Guided setup for playit install + claim flow."""

    __gsignals__ = {
        "setup-complete": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, server_manager: ServerManager, server_info: ServerInfo, server_running: bool):
        super().__init__()
        self._server_manager = server_manager
        self._server_info = server_info

        self._claim_url = ""
        self._setup_started = False
        self._finalize_started = False
        self._finished = False

        self.set_title("Set Up Playit")
        self.set_content_width(520)
        self.set_content_height(520)

        self._toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        self._close_btn = Gtk.Button(label="Cancel")
        self._close_btn.connect("clicked", self._on_close_clicked)
        header.pack_start(self._close_btn)

        self._action_btn = Gtk.Button(label="Setting up...")
        self._action_btn.add_css_class("suggested-action")
        self._action_btn.set_sensitive(False)
        self._action_btn.connect("clicked", self._on_action_clicked)
        header.pack_end(self._action_btn)

        self._toolbar_view.add_top_bar(header)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)

        self._stack.add_named(self._build_progress_page(), "progress")
        self._stack.add_named(self._build_claim_page(), "claim")
        self._stack.add_named(self._build_success_page(), "success")

        self._toolbar_view.set_content(self._stack)
        self.set_child(self._toolbar_view)

    def _build_progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(24)
        box.set_margin_end(24)

        self._progress_status = Adw.StatusPage()
        self._progress_status.set_icon_name("folder-download-symbolic")
        self._progress_status.set_title("Preparing Playit")
        self._progress_status.set_description("Starting setup...")

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)

        self._progress_detail = Gtk.Label(label="")
        self._progress_detail.set_xalign(0)
        self._progress_detail.add_css_class("dim-label")

        box.append(self._progress_status)
        box.append(self._progress_bar)
        box.append(self._progress_detail)
        return box

    def _build_claim_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title="Claim Playit",
            description="Open the claim page to continue setup",
        )

        self._claim_link_row = Adw.ActionRow(title="Claim link", subtitle="Waiting for link...")
        self._claim_link_row.set_activatable(True)
        self._claim_link_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._claim_link_row.connect("activated", self._on_open_claim_link)
        group.add(self._claim_link_row)

        page.add(group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(page)
        return scrolled

    def _build_success_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("object-select-symbolic")
        status.set_title("Finish setup from the link")
        status.set_description("Complete Playit setup in the browser link to finish connecting this server.")
        return status

    def _on_close_clicked(self, *_args):
        self.close()

    def _on_action_clicked(self, *_args):
        page = self._stack.get_visible_child_name()
        if page == "success":
            self.close()

    def start_setup(self):
        if self._setup_started:
            return
        self._setup_started = True
        self._action_btn.set_sensitive(False)
        self._close_btn.set_sensitive(False)
        self._stack.set_visible_child_name("progress")
        threading.Thread(target=self._setup_thread, daemon=True).start()

    def _update_progress(self, fraction: float, title: str, detail: str):
        def ui():
            self._progress_bar.set_fraction(max(0.0, min(1.0, fraction)))
            self._progress_status.set_description(title)
            self._progress_detail.set_label(detail)

        GLib.idle_add(ui)

    def _show_error(self, message: str):
        def ui():
            self._progress_status.set_icon_name("dialog-error-symbolic")
            self._progress_status.set_title("Setup failed")
            self._progress_status.set_description(message)
            self._progress_bar.set_fraction(0.0)
            self._progress_detail.set_label("You can close this dialog and try again.")
            self._close_btn.set_sensitive(True)
            self._action_btn.set_sensitive(False)
            self._action_btn.set_label("Failed")

        GLib.idle_add(ui)

    def _setup_thread(self):
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

        # Wait for claim URL from the live agent process.
        self._update_progress(0.7, "Waiting for claim link...", "")
        deadline = time.monotonic() + 60.0
        claimed_since = 0.0
        while time.monotonic() < deadline:
            if manager.claim_url:
                self._claim_url = manager.claim_url
                self._show_claim_page()
                return

            if manager.has_claimed_secret() and manager.is_running_for(server_id):
                # If this agent is already claimed, playit may not emit a claim link.
                if claimed_since == 0.0:
                    claimed_since = time.monotonic()
                elif time.monotonic() - claimed_since >= 2.0:
                    self._mark_setup_complete(binary, manager)
                    return
            else:
                claimed_since = 0.0

            if manager.public_endpoint and not manager.claim_url:
                # Already claimed and connected on this device.
                self._mark_setup_complete(binary, manager)
                return
            if not manager.is_running_for(server_id):
                self._show_error("Playit stopped while waiting for claim link.")
                return
            time.sleep(0.2)

        self._show_error("Playit did not provide a claim link in time.")

    def _run_playit_command(self, command: list[str], timeout: int) -> str:
        ok, output = self._run_playit_command_result(command, timeout)
        if not ok:
            return ""
        return output

    def _run_playit_command_result(self, command: list[str], timeout: int) -> tuple[bool, str]:
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
            return result.returncode == 0, output
        except Exception:
            return False, ""

    def _show_claim_page(self):
        def ui():
            self._claim_link_row.set_subtitle(self._claim_url)
            self._stack.set_visible_child_name("claim")
            self._close_btn.set_sensitive(True)
            self._action_btn.set_sensitive(False)
            self._action_btn.set_label("Waiting for browser...")

        GLib.idle_add(ui)

    def _begin_finish_setup(self):
        if self._finalize_started:
            return
        self._finalize_started = True

        self._close_btn.set_sensitive(False)
        self._action_btn.set_sensitive(False)
        self._action_btn.set_label("Finalizing...")
        self._stack.set_visible_child_name("progress")
        self._update_progress(
            0.85,
            "Waiting for browser approval...",
            "In browser: Continue, set name, Add agent. Keep Playit running.",
        )

        threading.Thread(target=self._finish_setup_thread, daemon=True).start()

    def _finish_setup_thread(self):
        manager = self._server_manager.playit_manager
        server_id = self._server_info.id
        binary = manager.resolve_binary()
        if not binary:
            self._show_error("Playit binary is missing. Run setup again.")
            return

        # Wait for the running agent to become claimed.
        deadline = time.monotonic() + 600.0
        while time.monotonic() < deadline:
            if manager.has_claimed_secret():
                self._mark_setup_complete(binary, manager)
                return
            if manager.public_endpoint:
                # Some setups may become connected before secret file is visible.
                self._mark_setup_complete(binary, manager)
                return
            if manager.claim_url and manager.claim_url != self._claim_url:
                self._claim_url = manager.claim_url
            if not manager.is_running_for(server_id):
                self._show_error("Playit agent stopped before claim completed.")
                break
            time.sleep(0.25)

        if self._finished:
            return
        self._show_error("Timed out waiting for browser claim to complete.")

    def _mark_setup_complete(self, binary: str, manager):
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

        self._finished = True

        def ui():
            self._stack.set_visible_child_name("success")
            self._close_btn.set_sensitive(False)
            self._action_btn.set_sensitive(True)
            self._action_btn.set_label("Done")
            self.emit("setup-complete")

        GLib.idle_add(ui)

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

    def _on_open_claim_link(self, *_args):
        if not self._claim_url:
            return
        if not _open_uri(self._claim_url):
            self._show_error("Could not open browser for the claim link.")
            return
        self._begin_finish_setup()
