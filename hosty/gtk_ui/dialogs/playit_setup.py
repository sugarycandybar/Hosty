"""
Playit setup dialog.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk

from hosty.gtk_ui.dialogs.dialog_common import (
    apply_dialog_size,
    build_progress_page,
    make_dialog_header,
    wrap_in_scrolled,
)
from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs


def _open_uri(uri: str) -> bool:
    try:
        if webbrowser.open(uri):
            return True
    except Exception:
        pass

    try:
        cmd = ["open", uri] if sys.platform == "darwin" else ["xdg-open", uri]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            **hidden_subprocess_kwargs(),
        )
        return True
    except Exception:
        return False


class PlayitSetupDialog(Adw.Dialog):
    """Guided setup for playit install + official claim flow."""

    __gsignals__ = {
        "setup-complete": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    _CLAIM_POLL_INTERVAL_SECONDS = 2.0
    _CLAIM_TIMEOUT_SECONDS = 600
    _CLAIM_MAX_CONSECUTIVE_ERRORS = 15

    def __init__(self, server_manager: ServerManager, server_info: ServerInfo, server_running: bool):
        super().__init__()
        self._server_manager = server_manager
        self._server_info = server_info

        self._claim_url = ""
        self._setup_started = False
        self._finished = False
        self._claim_poll_stop = False
        self._did_try_open_setup_link = False

        apply_dialog_size(self, _("Set Up Playit"))

        self._toolbar_view = Adw.ToolbarView()

        header, self._close_btn, self._action_btn = make_dialog_header(
            cancel_label=_("Cancel"),
            primary_label=_("Next"),
        )
        self._close_btn.connect("clicked", self._on_close_clicked)
        self._action_btn.connect("clicked", self._on_action_clicked)

        self._toolbar_view.add_top_bar(header)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)

        self._stack.add_named(self._build_steps_page(), "steps")
        self._stack.add_named(self._build_progress_page(), "progress")
        self._stack.add_named(self._build_claim_page(), "claim")
        self._stack.add_named(self._build_success_page(), "success")

        self._toolbar_view.set_content(self._stack)
        self.set_child(self._toolbar_view)

        self.connect("closed", self._on_closed)

    def _build_steps_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("system-software-install-symbolic")
        status.set_title(_("Playit Installation Steps"))
        status.set_description(
            _("1. Download Playit agent\n2. Approve the link in your browser\n3. Your tunnels start automatically")
        )
        return status

    def _build_progress_page(self) -> Gtk.Widget:
        box, self._progress_status, self._progress_bar, self._progress_detail = build_progress_page(
            icon_name="folder-download-symbolic",
            title=_("Preparing Playit"),
            description=_("Starting setup..."),
        )
        return box

    def _build_claim_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title=_("Link Playit"),
            description=_("Open the link and approve the agent in your browser"),
        )

        self._claim_link_row = Adw.ActionRow(title=_("Approval link"), subtitle=_("Waiting for link..."))
        self._claim_link_row.set_activatable(True)
        self._claim_link_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._claim_link_row.connect("activated", self._on_open_claim_link)
        group.add(self._claim_link_row)

        self._claim_status_row = Adw.ActionRow(title=_("Status"), subtitle=_("Waiting for your approval..."))
        self._claim_spinner = Gtk.Spinner()
        self._claim_spinner.set_visible(False)
        self._claim_status_row.add_suffix(self._claim_spinner)
        group.add(self._claim_status_row)

        page.add(group)

        return wrap_in_scrolled(page)

    def _build_success_page(self) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_icon_name("object-select-symbolic")
        status.set_title(_("Playit setup complete"))
        status.set_description(_("Your account is linked and tunnel startup is ready for this server."))
        return status

    def _on_close_clicked(self, *_args):
        self.close()

    def _on_closed(self, *_args):
        self._claim_poll_stop = True
        if not self._finished and self._server_manager:
            try:
                self._server_manager.playit_manager.abandon_claim()
            except Exception:
                pass

    def _on_action_clicked(self, *_args):
        page = self._stack.get_visible_child_name()
        if page == "steps":
            self._begin_checking()
        elif page == "success":
            self.close()
        # The claim page completes automatically; there is nothing to confirm.

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
            self._stack.set_visible_child_name("progress")
            self._progress_status.set_icon_name("dialog-error-symbolic")
            self._progress_status.set_title(_("Setup failed"))
            self._progress_status.set_description(message)
            self._progress_bar.set_fraction(0.0)
            self._progress_detail.set_label(_("You can close this dialog and try again."))
            self._close_btn.set_sensitive(True)
            self._action_btn.set_sensitive(False)
            self._action_btn.set_label(_("Failed"))

        GLib.idle_add(ui)

    def _setup_thread(self):
        manager = self._server_manager.playit_manager

        self._update_progress(0.2, _("Checking Playit installation..."), "")
        binary = manager.resolve_binary()
        if not binary:
            ok, msg = manager.install_latest_binary()
            if not ok:
                self._show_error(_("Could not install Playit: {}").format(msg))
                return
            binary = manager.resolve_binary()

        if not binary:
            self._show_error(_("Playit install completed but binary path was not found."))
            return

        if manager.has_claimed_secret():
            linked_ok, _detail = manager.validate_existing_link(retry_attempts=3)
            # A linked-but-assignable agent has a read-only key and cannot
            # create tunnels, so relink it as self-managed rather than
            # reporting success.
            if linked_ok and manager.is_agent_self_managed():
                self._mark_setup_complete(manager)
                return

        self._claim_url = manager.start_claim()
        self._show_claim_page()
        self._poll_claim(manager)

    def _show_claim_page(self):
        def ui():
            self._claim_link_row.set_subtitle(self._claim_url)
            self._claim_status_row.set_subtitle(_("Waiting for your approval..."))
            self._claim_spinner.set_visible(True)
            self._claim_spinner.set_spinning(True)
            self._stack.set_visible_child_name("claim")
            self._close_btn.set_sensitive(True)
            self._action_btn.set_sensitive(False)
            self._action_btn.set_label(_("Waiting..."))
            if self._claim_url and not self._did_try_open_setup_link:
                self._did_try_open_setup_link = True
                _open_uri(self._claim_url)

        GLib.idle_add(ui)

    def _poll_claim(self, manager):
        deadline = time.monotonic() + self._CLAIM_TIMEOUT_SECONDS
        consecutive_errors = 0

        while not self._claim_poll_stop:
            if time.monotonic() >= deadline:
                self._show_error(_("Timed out waiting for approval. Close this dialog and try again."))
                return

            try:
                state = manager.poll_claim()
            except Exception:
                state = "error"

            if state == "accepted":
                self._finish_claim(manager)
                return
            if state == "rejected":
                self._show_error(_("The claim was rejected in the browser. Close this dialog and try again."))
                return
            if state == "expired":
                self._show_error(_("This claim link has expired. Close this dialog and try again."))
                return
            if state == "invalid":
                self._show_error(_("This claim link is invalid. Close this dialog and try again."))
                return
            if state == "error":
                consecutive_errors += 1
                if consecutive_errors >= self._CLAIM_MAX_CONSECUTIVE_ERRORS:
                    self._show_error(
                        _("Could not reach playit to confirm the link. Check your connection and try again.")
                    )
                    return
            else:
                consecutive_errors = 0

            # Sleep in small slices so cancelling the dialog stops promptly.
            slept = 0.0
            while slept < self._CLAIM_POLL_INTERVAL_SECONDS and not self._claim_poll_stop:
                time.sleep(0.1)
                slept += 0.1

    def _finish_claim(self, manager):
        if self._claim_poll_stop:
            return
        self._update_progress(0.9, _("Linking account..."), _("Finishing playit setup."))
        ok, msg = manager.finish_claim()
        if not ok:
            self._show_error(_("Could not link playit account: {}").format(msg))
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
                self._show_error(_("Account linked, but could not start Playit: {}").format(start_msg))
                return

        self._finished = True

        def ui():
            self._claim_spinner.set_spinning(False)
            self._claim_spinner.set_visible(False)
            self._stack.set_visible_child_name("success")
            self._close_btn.set_sensitive(False)
            self._action_btn.set_sensitive(True)
            self._action_btn.set_label(_("Done"))
            self.emit("setup-complete")

        GLib.idle_add(ui)

    def _on_open_claim_link(self, *_args):
        if not self._claim_url:
            return
        _open_uri(self._claim_url)
