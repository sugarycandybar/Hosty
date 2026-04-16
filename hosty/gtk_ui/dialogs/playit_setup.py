"""
Playit setup dialog.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk

from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager


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
    """Guided setup for playit install + setup-code link flow."""

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
        self._did_try_open_setup_link = False

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

        self._action_btn = Gtk.Button(label="Next")
        self._action_btn.add_css_class("suggested-action")
        self._action_btn.connect("clicked", self._on_action_clicked)
        header.pack_end(self._action_btn)

        self._toolbar_view.add_top_bar(header)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)

        self._stack.add_named(self._build_steps_page(), "steps")
        self._stack.add_named(self._build_progress_page(), "progress")
        self._stack.add_named(self._build_claim_page(), "claim")
        self._stack.add_named(self._build_success_page(), "success")

        self._toolbar_view.set_content(self._stack)
        self.set_child(self._toolbar_view)

    def _build_steps_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("system-software-install-symbolic")
        status.set_title("Playit Installation Steps")
        status.set_description(
            "1. Download Playit agent\n"
            "2. Open the Hosty setup URL\n"
            "3. Paste your setup code\n"
            "4. Link account and start tunnel"
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
            title="Link Playit",
            description="Open the setup link and paste the setup code",
        )

        self._claim_link_row = Adw.ActionRow(title="Setup link", subtitle="Waiting for link...")
        self._claim_link_row.set_activatable(True)
        self._claim_link_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._claim_link_row.connect("activated", self._on_open_claim_link)
        group.add(self._claim_link_row)

        self._setup_code_row = Adw.EntryRow(title="Setup code")
        self._setup_code_row.set_show_apply_button(False)
        self._setup_code_row.set_text("")
        group.add(self._setup_code_row)

        page.add(group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(page)
        return scrolled

    def _build_success_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("object-select-symbolic")
        status.set_title("Playit setup complete")
        status.set_description("Your account is linked and tunnel startup is ready for this server.")
        return status

    def _on_close_clicked(self, *_args):
        self.close()

    def _on_action_clicked(self, *_args):
        page = self._stack.get_visible_child_name()
        if page == "steps":
            self._begin_checking()
        elif page == "claim":
            self._begin_finish_setup()
        elif page == "success":
            self.close()

    def start_setup(self):
        if self._setup_started:
            return
        self._setup_started = True
        self._begin_checking()

    def _begin_checking(self):
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
        self._show_claim_page()

    def _show_claim_page(self):
        def ui():
            self._claim_link_row.set_subtitle(self._claim_url)
            self._stack.set_visible_child_name("claim")
            self._close_btn.set_sensitive(True)
            self._action_btn.set_sensitive(True)
            self._action_btn.set_label("Link Code")
            if self._claim_url and not self._did_try_open_setup_link:
                self._did_try_open_setup_link = True
                _open_uri(self._claim_url)

        GLib.idle_add(ui)

    def _begin_finish_setup(self):
        if self._finalize_started:
            return

        setup_code = self._setup_code_row.get_text().strip()
        if not setup_code:
            self._show_error("Paste the setup code from the browser first.")
            return

        self._finalize_started = True
        self._close_btn.set_sensitive(False)
        self._action_btn.set_sensitive(False)
        self._action_btn.set_label("Linking...")
        self._stack.set_visible_child_name("progress")
        self._update_progress(
            0.85,
            "Linking account...",
            "Exchanging setup code with playit.",
        )

        threading.Thread(target=self._finish_setup_thread, args=(setup_code,), daemon=True).start()

    def _finish_setup_thread(self, setup_code: str):
        manager = self._server_manager.playit_manager
        ok, msg = manager.link_account(setup_code)
        if not ok:
            self._show_error(f"Could not link playit account: {msg}")
            return

        self._mark_setup_complete(manager)

    def _mark_setup_complete(self, manager):
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

        self._finished = True

        def ui():
            self._stack.set_visible_child_name("success")
            self._close_btn.set_sensitive(False)
            self._action_btn.set_sensitive(True)
            self._action_btn.set_label("Done")
            self.emit("setup-complete")

        GLib.idle_add(ui)

    def _on_open_claim_link(self, *_args):
        if not self._claim_url:
            return
        _open_uri(self._claim_url)
