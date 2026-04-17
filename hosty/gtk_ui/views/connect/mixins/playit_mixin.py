"""
ConnectView - Server connection tools (playit.gg tunnel).
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Adw, Gdk, GLib

from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.gtk_ui.dialogs.playit_setup import PlayitSetupDialog


PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"



from ..utils import *

class PlayitMixin:
    def _load_server_config(self):
        root = self._server_dir()
        if not root:
            self._cfg = {}
            return
        self._cfg = load_playit_config(root)

        if self._server_manager:
            claimed_secret = self._server_manager.playit_manager.read_claimed_secret()
            cfg_changed = False
            if claimed_secret and claimed_secret != str(self._cfg.get("secret", "")).strip():
                self._cfg["secret"] = claimed_secret
                cfg_changed = True

            # If playit is already claimed globally, auto-heal per-server flags.
            if claimed_secret and not bool(self._cfg.get("enabled", False)):
                self._cfg["enabled"] = True
                cfg_changed = True
            if claimed_secret and not bool(self._cfg.get("setup_complete", False)):
                self._cfg["setup_complete"] = True
                cfg_changed = True

            if cfg_changed:
                save_playit_config(root, self._cfg)

        self._suppress_config_updates = True
        self._auto_start_row.set_active(bool(self._cfg.get("auto_start", True)))
        self._suppress_config_updates = False

    def _save_server_config(self, updates: Optional[dict] = None) -> bool:
        root = self._server_dir()
        if not root:
            return False

        if updates:
            self._cfg.update(updates)

        return save_playit_config(
            root,
            {
                "secret": str(self._cfg.get("secret", "")).strip(),
                "enabled": bool(self._cfg.get("enabled", False)),
                "setup_complete": bool(self._cfg.get("setup_complete", False)),
                "auto_start": self._auto_start_row.get_active(),
                "auto_install": bool(self._cfg.get("auto_install", True)),
                "java_endpoint": str(self._cfg.get("java_endpoint", "")).strip(),
                "bedrock_endpoint": str(self._cfg.get("bedrock_endpoint", "")).strip(),
            },
        )

    def _on_auto_start_toggled(self, *_args):
        if self._suppress_config_updates:
            return
        self._save_server_config()

        if self._server_info and self._auto_start_row.get_active():
            root = self.get_root()
            if root and hasattr(root, "clear_playit_auto_start_pause"):
                root.clear_playit_auto_start_pause(self._server_info.id)

    def _is_setup_complete(self) -> bool:
        if not self._server_manager:
            return False
        return bool(
            self._cfg.get("enabled", False)
            and self._cfg.get("setup_complete", False)
            and (
                self._server_manager.playit_manager.has_claimed_secret()
                or bool(str(self._cfg.get("secret", "")).strip())
            )
        )

    def _refresh_mode(self):
        mode = "ready" if self._is_setup_complete() else "setup"
        self._mode_stack.set_visible_child_name(mode)

    def _refresh_status_row(self):
        if not self._server_manager:
            self._tunnel_row.set_subtitle("Stopped")
            self._tunnel_domain_row.set_subtitle("Not available")
            self._tunnel_domain_row.set_activatable(False)
            self._copy_tunnel_domain_btn.set_sensitive(False)
            self._bedrock_domain_row.set_subtitle("Not available")
            self._bedrock_domain_row.set_activatable(False)
            self._copy_bedrock_domain_btn.set_sensitive(False)
            self._java_tunnel_action_btn.set_label("")
            self._java_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._java_tunnel_action_btn.set_tooltip_text("Add Java tunnel")
            self._java_tunnel_action_btn.remove_css_class("pill")
            self._java_tunnel_action_btn.add_css_class("flat")
            self._java_tunnel_action_btn.set_sensitive(False)
            self._delete_java_tunnel_btn.set_sensitive(False)
            self._delete_java_tunnel_btn.set_visible(False)
            self._bedrock_tunnel_action_btn.set_label("")
            self._bedrock_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._bedrock_tunnel_action_btn.set_tooltip_text("Add Bedrock tunnel")
            self._bedrock_tunnel_action_btn.remove_css_class("pill")
            self._bedrock_tunnel_action_btn.add_css_class("flat")
            self._bedrock_tunnel_action_btn.set_sensitive(False)
            self._delete_bedrock_tunnel_btn.set_sensitive(False)
            self._delete_bedrock_tunnel_btn.set_visible(False)
            self._tunnel_btn.set_label("Start Agent")
            self._tunnel_btn.remove_css_class("destructive-action")
            self._tunnel_btn.add_css_class("suggested-action")
            self._tunnel_btn.remove_css_class("hosty-starting-button")
            return

        playit = self._server_manager.playit_manager
        endpoint = str(playit.public_endpoint or "").strip()
        endpoint_for_this_server = ""
        agent_running_for_this_server = False
        if playit.is_running:
            if self._server_info and playit.server_id == self._server_info.id:
                agent_running_for_this_server = True
                self._tunnel_row.set_subtitle("Running for this server")
                endpoint_for_this_server = endpoint
                self._tunnel_btn.set_label("Stop")
                self._tunnel_btn.remove_css_class("suggested-action")
                self._tunnel_btn.add_css_class("destructive-action")
                self._tunnel_btn.set_sensitive(True)
            else:
                self._tunnel_row.set_subtitle("Running for another server")
                self._tunnel_btn.set_label("Start")
                self._tunnel_btn.remove_css_class("destructive-action")
                self._tunnel_btn.add_css_class("suggested-action")
                self._tunnel_btn.set_sensitive(False)
        else:
            self._tunnel_row.set_subtitle("Stopped")
            self._tunnel_btn.set_label("Start")
            self._tunnel_btn.remove_css_class("destructive-action")
            self._tunnel_btn.add_css_class("suggested-action")
            self._tunnel_btn.set_sensitive(True)

        java_endpoint = str(self._cfg.get("java_endpoint", "")).strip()
        if endpoint_for_this_server:
            java_endpoint = endpoint_for_this_server
            if java_endpoint != str(self._cfg.get("java_endpoint", "")).strip():
                self._save_server_config({"java_endpoint": java_endpoint})

        if java_endpoint:
            self._tunnel_domain_row.set_subtitle(java_endpoint)
            self._tunnel_domain_row.set_activatable(True)
            self._copy_tunnel_domain_btn.set_sensitive(True)
        else:
            self._tunnel_domain_row.set_subtitle("Not available")
            self._tunnel_domain_row.set_activatable(False)
            self._copy_tunnel_domain_btn.set_sensitive(False)

        bedrock_endpoint = str(self._cfg.get("bedrock_endpoint", "")).strip()
        if bedrock_endpoint:
            self._bedrock_domain_row.set_subtitle(bedrock_endpoint)
            self._bedrock_domain_row.set_activatable(True)
            self._copy_bedrock_domain_btn.set_sensitive(True)
        else:
            self._bedrock_domain_row.set_subtitle("Not available")
            self._bedrock_domain_row.set_activatable(False)
            self._copy_bedrock_domain_btn.set_sensitive(False)

        if java_endpoint:
            self._java_tunnel_action_btn.set_label("")
            self._java_tunnel_action_btn.set_icon_name("view-refresh-symbolic")
            self._java_tunnel_action_btn.set_tooltip_text("Regenerate Java tunnel")
            self._java_tunnel_action_btn.remove_css_class("pill")
            self._java_tunnel_action_btn.add_css_class("flat")
            self._delete_java_tunnel_btn.set_visible(True)
            self._delete_java_tunnel_btn.set_sensitive(True)
        else:
            self._java_tunnel_action_btn.set_label("")
            self._java_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._java_tunnel_action_btn.set_tooltip_text("Add Java tunnel")
            self._java_tunnel_action_btn.remove_css_class("flat")
            self._java_tunnel_action_btn.add_css_class("flat")
            self._delete_java_tunnel_btn.set_visible(False)
            self._delete_java_tunnel_btn.set_sensitive(False)

        if bedrock_endpoint:
            self._bedrock_tunnel_action_btn.set_label("")
            self._bedrock_tunnel_action_btn.set_icon_name("view-refresh-symbolic")
            self._bedrock_tunnel_action_btn.set_tooltip_text("Regenerate Bedrock tunnel")
            self._bedrock_tunnel_action_btn.remove_css_class("pill")
            self._bedrock_tunnel_action_btn.add_css_class("flat")
            self._delete_bedrock_tunnel_btn.set_visible(True)
            self._delete_bedrock_tunnel_btn.set_sensitive(True)
        else:
            self._bedrock_tunnel_action_btn.set_label("")
            self._bedrock_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._bedrock_tunnel_action_btn.set_tooltip_text("Add Bedrock tunnel")
            self._bedrock_tunnel_action_btn.remove_css_class("flat")
            self._bedrock_tunnel_action_btn.add_css_class("flat")
            self._delete_bedrock_tunnel_btn.set_visible(False)
            self._delete_bedrock_tunnel_btn.set_sensitive(False)

        tunnel_actions_locked = bool(
            playit.is_running
            or self._start_in_progress
            or self._java_tunnel_in_progress
            or self._bedrock_in_progress
        )
        self._java_tunnel_action_btn.set_sensitive(not tunnel_actions_locked)
        self._bedrock_tunnel_action_btn.set_sensitive(not tunnel_actions_locked)
        if tunnel_actions_locked:
            self._delete_java_tunnel_btn.set_sensitive(False)
            self._delete_bedrock_tunnel_btn.set_sensitive(False)

        if self._start_in_progress:
            self._tunnel_btn.set_label("Starting Agent...")
            self._tunnel_btn.set_sensitive(False)
            self._tunnel_btn.add_css_class("hosty-starting-button")
        elif self._java_tunnel_in_progress or self._bedrock_in_progress:
            self._tunnel_btn.set_sensitive(False)
            self._tunnel_btn.remove_css_class("hosty-starting-button")
        else:
            self._tunnel_btn.remove_css_class("hosty-starting-button")

    def _on_playit_status_changed(self, *_args):
        self._refresh_status_row()

    def _on_playit_endpoint_changed(self, *_args):
        self._refresh_status_row()

    def _on_copy_tunnel_domain(self, *_args):
        endpoint = str(self._cfg.get("java_endpoint", "")).strip()
        if not endpoint and self._server_manager and self._server_info:
            playit = self._server_manager.playit_manager
            if playit.is_running_for(self._server_info.id):
                endpoint = str(playit.public_endpoint or "").strip()
        if not endpoint:
            return

        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            clipboard = display.get_clipboard()
            clipboard.set(endpoint)
            self._toast("Java tunnel domain copied")
        except Exception:
            pass

    def _on_copy_bedrock_domain(self, *_args):
        endpoint = str(self._cfg.get("bedrock_endpoint", "")).strip()
        if not endpoint:
            return

        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            clipboard = display.get_clipboard()
            clipboard.set(endpoint)
            self._toast("Bedrock tunnel domain copied")
        except Exception:
            pass

    def _on_tunnel_toggle(self, *_args):
        if not self._server_manager:
            return
        playit = self._server_manager.playit_manager
        if playit.is_running and self._server_info and playit.server_id == self._server_info.id:
            self._on_stop()
        else:
            self._on_start()

    def _on_open_setup_dialog(self, *_args):
        if not self._server_manager or not self._server_info:
            return

        dialog = PlayitSetupDialog(
            self._server_manager,
            self._server_info,
            self._server_running(),
        )
        dialog.connect("setup-complete", self._on_setup_complete)
        dialog.present(self.get_root())
        dialog.start_setup()

    def _on_setup_complete(self, *_args):
        self._load_server_config()
        self._refresh_mode()
        self._refresh_status_row()
        self._toast("Playit setup completed")

    def _on_open_dashboard(self, *_args):
        if not _open_uri(PLAYIT_DASHBOARD_URL):
            self._alert("Could not open browser", "Unable to open playit dashboard.")

    def _confirm_delete_tunnel(self, tunnel_name: str, on_confirm):
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Delete {tunnel_name} tunnel?")
        dialog.set_body(
            "This will remove the current tunnel domain for this server. "
            "You can add a new tunnel again later."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "delete":
                on_confirm()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _on_regenerate_domain(self, *_args):
        # Backward-compatible handler alias.
        self._on_manage_java_tunnel()

    def _on_manage_java_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        playit = self._server_manager.playit_manager
        if playit.is_running:
            self._alert("Stop agent first", "Stop the playit agent before changing Java tunnel settings.")
            return
        if self._start_in_progress:
            self._toast("Playit startup is already in progress")
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress:
            self._toast("A tunnel operation is already in progress")
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        had_java_tunnel = bool(str(self._cfg.get("java_endpoint", "")).strip())
        self._java_tunnel_in_progress = True
        self._refresh_status_row()

        def run():
            if had_java_tunnel:
                ok, msg, endpoint = self._server_manager.playit_manager.regenerate_java_tunnel(
                    server_id,
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )
            else:
                ok, msg, endpoint = self._server_manager.playit_manager.add_java_tunnel(
                    server_id,
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )

            def ui_done():
                self._java_tunnel_in_progress = False
                if ok and endpoint:
                    self._save_server_config({"java_endpoint": endpoint})
                self._refresh_status_row()
                if ok:
                    self._toast(msg)
                else:
                    self._alert("Could not update Java tunnel", msg)

            GLib.idle_add(ui_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_manage_bedrock_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        playit = self._server_manager.playit_manager
        if playit.is_running:
            self._alert("Stop agent first", "Stop the playit agent before changing Bedrock tunnel settings.")
            return
        if self._start_in_progress:
            self._toast("Playit startup is already in progress")
            return
        if self._java_tunnel_in_progress:
            self._toast("Java tunnel operation is already in progress")
            return
        if self._bedrock_in_progress:
            self._toast("Bedrock tunnel creation is already in progress")
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        had_bedrock_tunnel = bool(str(self._cfg.get("bedrock_endpoint", "")).strip())
        self._bedrock_in_progress = True
        self._refresh_status_row()

        def run():
            if had_bedrock_tunnel:
                ok, msg, endpoint = self._server_manager.playit_manager.regenerate_bedrock_tunnel(
                    server_id,
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )
            else:
                ok, msg, endpoint = self._server_manager.playit_manager.add_bedrock_tunnel(
                    server_id,
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )

            def ui_done():
                self._bedrock_in_progress = False
                if ok and endpoint:
                    self._save_server_config({"bedrock_endpoint": endpoint})
                self._refresh_status_row()
                if ok:
                    self._toast(msg)
                else:
                    self._alert("Could not update Bedrock tunnel", msg)

            GLib.idle_add(ui_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_delete_java_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        playit = self._server_manager.playit_manager
        if playit.is_running:
            self._alert("Stop agent first", "Stop the playit agent before deleting Java tunnel.")
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress or self._start_in_progress:
            self._toast("A tunnel operation is already in progress")
            return

        def confirmed_delete():
            self._java_tunnel_in_progress = True
            self._refresh_status_row()
            server_dir = str(self._server_info.server_dir)
            secret = str(self._cfg.get("secret", "")).strip()

            def run():
                ok, msg = self._server_manager.playit_manager.delete_java_tunnel(
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )

                def ui_done():
                    self._java_tunnel_in_progress = False
                    if ok or "No java tunnel found" in str(msg):
                        self._save_server_config({"java_endpoint": ""})
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    elif "No java tunnel found" in str(msg):
                        self._toast("Java tunnel already missing")
                    else:
                        self._alert("Could not delete Java tunnel", msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        self._confirm_delete_tunnel("Java", confirmed_delete)

    def _on_delete_bedrock_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        playit = self._server_manager.playit_manager
        if playit.is_running:
            self._alert("Stop agent first", "Stop the playit agent before deleting Bedrock tunnel.")
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress or self._start_in_progress:
            self._toast("A tunnel operation is already in progress")
            return

        def confirmed_delete():
            self._bedrock_in_progress = True
            self._refresh_status_row()
            server_dir = str(self._server_info.server_dir)
            secret = str(self._cfg.get("secret", "")).strip()

            def run():
                ok, msg = self._server_manager.playit_manager.delete_bedrock_tunnel(
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )

                def ui_done():
                    self._bedrock_in_progress = False
                    if ok or "No bedrock tunnel found" in str(msg):
                        self._save_server_config({"bedrock_endpoint": ""})
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    elif "No bedrock tunnel found" in str(msg):
                        self._toast("Bedrock tunnel already missing")
                    else:
                        self._alert("Could not delete Bedrock tunnel", msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        self._confirm_delete_tunnel("Bedrock", confirmed_delete)

    def _on_start(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress:
            self._toast("A tunnel operation is already in progress")
            return
        if self._start_in_progress:
            self._toast("Playit startup is already in progress")
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        self._start_in_progress = True

        def worker():
            playit = self._server_manager.playit_manager
            return playit.start(
                server_id,
                server_dir,
                secret=secret,
                auto_install=True,
            )

        def run():
            ok, msg = worker()

            def ui_done():
                self._start_in_progress = False
                self._refresh_status_row()
                if ok:
                    self._toast("Playit agent started")
                else:
                    self._alert("Could not start playit", msg)

            GLib.idle_add(ui_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_stop(self, *_args):
        if not self._server_manager:
            return

        if self._server_info and self._auto_start_row.get_active() and self._server_running():
            root = self.get_root()
            if root and hasattr(root, "pause_playit_auto_start_for_running_server"):
                root.pause_playit_auto_start_for_running_server(self._server_info.id)

        ok, msg = self._server_manager.playit_manager.stop()
        self._refresh_status_row()
        if ok:
            self._toast("Playit agent stopped")
        else:
            self._alert("Could not stop playit", msg)

