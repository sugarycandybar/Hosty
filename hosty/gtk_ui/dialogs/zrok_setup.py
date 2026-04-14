"""
Zrok setup dialog for GTK UI.
"""
from __future__ import annotations

import threading
import sys
import webbrowser
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk, Gdk

from hosty.shared.backend.zrok_config import load_zrok_config, save_zrok_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager


def _open_uri(uri: str) -> bool:
    try:
        if webbrowser.open(uri):
            return True
    except Exception:
        pass

    try:
        cmd = ["open", uri] if sys.platform == "darwin" else ["xdg-open", uri]
        import subprocess
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


class ZrokSetupDialog(Adw.Dialog):
    """Guided setup for zrok install + claim flow."""

    __gsignals__ = {
        "setup-complete": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, server_manager: ServerManager, server_info: ServerInfo, server_running: bool):
        super().__init__()
        self._server_manager = server_manager
        self._server_info = server_info

        self._setup_started = False
        self._finished = False

        self.set_title("Set Up Zrok")
        self.set_content_width(520)
        self.set_content_height(520)

        self._toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        self._close_btn = Gtk.Button(label="Cancel")
        self._close_btn.connect("clicked", self._on_close_clicked)
        header.pack_start(self._close_btn)

        self._action_btn = Gtk.Button(label="Next")
        self._action_btn.add_css_class("suggested-action")
        self._action_btn.connect("clicked", self._on_action_clicked)
        header.pack_end(self._action_btn)

        self._toolbar_view.add_top_bar(header)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)

        self._stack.add_named(self._build_steps_page(), "steps")
        self._stack.add_named(self._build_progress_page(), "progress")
        self._stack.add_named(self._build_token_page(), "token")
        self._stack.add_named(self._build_success_page(), "success")

        self._toolbar_view.set_content(self._stack)
        self.set_child(self._toolbar_view)

    def _build_steps_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("system-software-install-symbolic")
        status.set_title("Zrok Installation Steps")
        status.set_description(
            "1. Download Zrok core\n"
            "2. Log in with your Zrok account\n"
            "3. Start tunnel"
        )
        return status

    def _build_progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(24)
        box.set_margin_end(24)

        self._progress_status = Adw.StatusPage()
        self._progress_status.set_icon_name("folder-download-symbolic")
        self._progress_status.set_title("Preparing Zrok")
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

    def _build_token_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title="Connect Zrok Account",
            description="Enter your Zrok email and password to retrieve your token. If you don't have an account, visit zrok.io.",
        )

        self._email_row = Adw.EntryRow()
        self._email_row.set_title("Email")
        group.add(self._email_row)

        self._password_row = Adw.PasswordEntryRow()
        self._password_row.set_title("Password")
        group.add(self._password_row)

        page.add(group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(page)
        return scrolled

    def _build_success_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("object-select-symbolic")
        status.set_title("Zrok Setup Complete")
        status.set_description("Your Zrok tunnel is ready to be started.")
        return status

    def _on_close_clicked(self, *_args):
        self.close()

    def _on_action_clicked(self, *_args):
        page = self._stack.get_visible_child_name()
        if page == "steps":
            self._begin_checking()
        elif page == "token":
            self._apply_token()
        elif page == "success":
            self.close()

    def start_setup(self):
        if self._setup_started:
            return
        self._setup_started = True
        self._stack.set_visible_child_name("steps")

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

    def _begin_checking(self):
        self._close_btn.set_sensitive(False)
        self._action_btn.set_sensitive(False)
        self._stack.set_visible_child_name("progress")
        threading.Thread(target=self._checking_thread, daemon=True).start()

    def _checking_thread(self):
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
            self._mark_setup_complete()
            return

        def ui():
            self._stack.set_visible_child_name("token")
            self._close_btn.set_sensitive(True)
            self._action_btn.set_sensitive(True)
            self._action_btn.set_label("Enable")

        GLib.idle_add(ui)

    def _apply_token(self):
        email = self._email_row.get_text().strip()
        password = self._password_row.get_text()
        if not email or not password:
            return

        self._close_btn.set_sensitive(False)
        self._action_btn.set_sensitive(False)
        self._action_btn.set_label("Authenticating...")
        self._stack.set_visible_child_name("progress")

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

    def _mark_setup_complete(self, token: str = ""):
        cfg = load_zrok_config(self._server_info.server_dir)
        cfg["enabled"] = True
        cfg["setup_complete"] = True
        cfg["auto_start"] = bool(cfg.get("auto_start", True))
        cfg["auto_install"] = True
        if token:
            cfg["token"] = token
        save_zrok_config(self._server_info.server_dir, cfg)

        self._finished = True

        def ui():
            self._stack.set_visible_child_name("success")
            self._close_btn.set_sensitive(False)
            self._action_btn.set_sensitive(True)
            self._action_btn.set_label("Done")
            self.emit("setup-complete")

        GLib.idle_add(ui)
