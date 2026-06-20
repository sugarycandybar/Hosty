"""
ConnectView - Server connection tools (playit.gg tunnel).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib

from hosty.gtk_ui.dialogs.manage_playit_tunnel import ManagePlayitTunnelDialog
from hosty.gtk_ui.dialogs.playit_setup import PlayitSetupDialog
from hosty.shared.backend.playit_config import load_playit_config, save_playit_config

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

    def _save_server_config(self, updates: dict | None = None) -> bool:
        root = self._server_dir()
        if not root:
            return False

        if updates:
            self._cfg.update(updates)

        # Load existing config from disk so disk-level changes (e.g. port reassignment
        # from server_detail view) are never overwritten by stale self._cfg values.
        existing = load_playit_config(root)
        existing.update(
            {
                "secret": str(self._cfg.get("secret", "")).strip(),
                "enabled": bool(self._cfg.get("enabled", False)),
                "setup_complete": bool(self._cfg.get("setup_complete", False)),
                "auto_start": self._auto_start_row.get_active(),
                "auto_install": bool(self._cfg.get("auto_install", True)),
                "java_endpoint": str(self._cfg.get("java_endpoint", "")).strip(),
                "bedrock_endpoint": str(self._cfg.get("bedrock_endpoint", "")).strip(),
                "voicechat_endpoint": str(self._cfg.get("voicechat_endpoint", "")).strip(),
                # Only update port if the caller explicitly passed it
                **({"bedrock_port": updates["bedrock_port"]} if updates and "bedrock_port" in updates else {}),
                **({"voicechat_port": updates["voicechat_port"]} if updates and "voicechat_port" in updates else {}),
            },
        )
        return save_playit_config(root, existing)

    def _propagate_tunnel_endpoint(self, endpoint_key: str, port: int, new_endpoint: str) -> None:
        if not self._server_manager:
            return

        for sid, info in self._server_manager._servers.items():
            if sid == self._server_info.id:
                continue
            try:
                cfg = load_playit_config(info.server_dir)
                if not str(cfg.get(endpoint_key, "")).strip():
                    continue
                if endpoint_key == "java_endpoint":
                    other_port = self._server_manager.playit_manager._read_server_port(str(info.server_dir))
                elif endpoint_key == "bedrock_endpoint":
                    other_port = int(cfg.get("bedrock_port", 19132))
                elif endpoint_key == "voicechat_endpoint":
                    other_port = int(cfg.get("voicechat_port", 24454))
                else:
                    continue
                if other_port != port:
                    continue
                cfg[endpoint_key] = new_endpoint
                playit = self._server_manager.playit_manager
                if sid in playit._active_server_ids:
                    playit._active_server_ids[sid]["endpoint"] = new_endpoint
                save_playit_config(
                    str(info.server_dir),
                    {
                        "secret": str(cfg.get("secret", "")).strip(),
                        "enabled": bool(cfg.get("enabled", False)),
                        "setup_complete": bool(cfg.get("setup_complete", False)),
                        "auto_start": bool(cfg.get("auto_start", True)),
                        "auto_install": bool(cfg.get("auto_install", True)),
                        "java_endpoint": str(cfg.get("java_endpoint", "")).strip(),
                        "bedrock_endpoint": str(cfg.get("bedrock_endpoint", "")).strip(),
                        "voicechat_endpoint": str(cfg.get("voicechat_endpoint", "")).strip(),
                        "bedrock_port": int(cfg.get("bedrock_port", 19132)),
                        "voicechat_port": int(cfg.get("voicechat_port", 24454)),
                    },
                )
            except Exception:
                pass

    def _has_other_server_with_tunnel_on_port(self, endpoint_key: str, port: int) -> bool:
        for sid, info in self._server_manager._servers.items():
            if sid == self._server_info.id:
                continue
            try:
                cfg = load_playit_config(info.server_dir)
                if not str(cfg.get(endpoint_key, "")).strip():
                    continue
                if endpoint_key == "java_endpoint":
                    other_port = self._server_manager.playit_manager._read_server_port(str(info.server_dir))
                elif endpoint_key == "bedrock_endpoint":
                    other_port = int(cfg.get("bedrock_port", 19132))
                elif endpoint_key == "voicechat_endpoint":
                    other_port = int(cfg.get("voicechat_port", 24454))
                else:
                    continue
                if other_port == port:
                    return True
            except Exception:
                continue
        return False

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
            self._tunnel_row.set_subtitle(_("Stopped"))
            self._tunnel_domain_row.set_subtitle(_("Not available"))
            self._tunnel_domain_row.set_activatable(False)
            self._copy_tunnel_domain_btn.set_sensitive(False)
            self._copy_tunnel_domain_btn.set_visible(False)
            self._bedrock_domain_row.set_subtitle(_("Not available"))
            self._bedrock_domain_row.set_activatable(False)
            self._copy_bedrock_domain_btn.set_sensitive(False)
            self._copy_bedrock_domain_btn.set_visible(False)
            self._voicechat_domain_row.set_subtitle(_("Not available"))
            self._voicechat_domain_row.set_activatable(False)
            self._copy_voicechat_domain_btn.set_sensitive(False)
            self._copy_voicechat_domain_btn.set_visible(False)
            self._java_tunnel_action_btn.set_label("")
            self._java_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._java_tunnel_action_btn.set_tooltip_text(_("Add Java tunnel"))
            self._java_tunnel_action_btn.remove_css_class("pill")
            self._java_tunnel_action_btn.add_css_class("flat")
            self._java_tunnel_action_btn.set_sensitive(False)
            self._bedrock_tunnel_action_btn.set_label("")
            self._bedrock_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._bedrock_tunnel_action_btn.set_tooltip_text(_("Add Bedrock tunnel"))
            self._bedrock_tunnel_action_btn.remove_css_class("pill")
            self._bedrock_tunnel_action_btn.add_css_class("flat")
            self._bedrock_tunnel_action_btn.set_sensitive(False)
            self._voicechat_tunnel_action_btn.set_label("")
            self._voicechat_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._voicechat_tunnel_action_btn.set_tooltip_text(_("Add Voice Chat tunnel"))
            self._voicechat_tunnel_action_btn.remove_css_class("pill")
            self._voicechat_tunnel_action_btn.add_css_class("flat")
            self._voicechat_tunnel_action_btn.set_sensitive(False)
            # Hide spinners on initial state
            self._java_tunnel_spinner.set_visible(False)
            self._java_tunnel_spinner.set_spinning(False)
            self._bedrock_tunnel_spinner.set_visible(False)
            self._bedrock_tunnel_spinner.set_spinning(False)
            self._voicechat_tunnel_spinner.set_visible(False)
            self._voicechat_tunnel_spinner.set_spinning(False)
            self._tunnel_btn.set_label(_("Start Agent"))
            self._tunnel_btn.remove_css_class("destructive-action")
            self._tunnel_btn.add_css_class("suggested-action")
            self._tunnel_btn.remove_css_class("hosty-starting-button")
            return

        playit = self._server_manager.playit_manager
        endpoint_for_this_server = playit.get_endpoint_for(self._server_info.id) if self._server_info else ""
        if playit.is_running:
            self._tunnel_row.set_subtitle(_("Running"))
            self._tunnel_btn.set_label(_("Stop"))
            self._tunnel_btn.remove_css_class("suggested-action")
            self._tunnel_btn.add_css_class("destructive-action")
            self._tunnel_btn.set_sensitive(True)
        else:
            self._tunnel_row.set_subtitle(_("Stopped"))
            self._tunnel_btn.set_label(_("Start"))
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
            self._copy_tunnel_domain_btn.set_visible(True)
        else:
            self._tunnel_domain_row.set_subtitle(_("Not available"))
            self._tunnel_domain_row.set_activatable(True)
            self._copy_tunnel_domain_btn.set_sensitive(False)
            self._copy_tunnel_domain_btn.set_visible(False)

        bedrock_endpoint = str(self._cfg.get("bedrock_endpoint", "")).strip()
        if bedrock_endpoint:
            # Format bedrock endpoint with middle dot separator and Port label (domain:port -> domain · Port port)
            if ":" in bedrock_endpoint:
                domain, port = bedrock_endpoint.rsplit(":", 1)
                formatted_endpoint = f"{domain} · Port {port}"
            else:
                formatted_endpoint = bedrock_endpoint
            self._bedrock_domain_row.set_subtitle(formatted_endpoint)
            self._bedrock_domain_row.set_activatable(True)
            self._copy_bedrock_domain_btn.set_sensitive(True)
            self._copy_bedrock_domain_btn.set_visible(True)
        else:
            self._bedrock_domain_row.set_subtitle(_("Not available"))
            self._bedrock_domain_row.set_activatable(True)
            self._copy_bedrock_domain_btn.set_sensitive(False)
            self._copy_bedrock_domain_btn.set_visible(False)

        if java_endpoint:
            self._java_tunnel_action_btn.set_label("")
            self._java_tunnel_action_btn.set_icon_name("emblem-system-symbolic")
            self._java_tunnel_action_btn.set_tooltip_text(_("Manage Java tunnel"))
            self._java_tunnel_action_btn.remove_css_class("pill")
            self._java_tunnel_action_btn.add_css_class("flat")
        else:
            self._java_tunnel_action_btn.set_label("")
            self._java_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._java_tunnel_action_btn.set_tooltip_text(_("Add Java tunnel"))
            self._java_tunnel_action_btn.remove_css_class("flat")
            self._java_tunnel_action_btn.add_css_class("flat")

        # Show spinner when in progress, button otherwise
        if self._java_tunnel_in_progress:
            self._java_tunnel_action_btn.set_visible(False)
            self._java_tunnel_spinner.set_visible(True)
            self._java_tunnel_spinner.set_spinning(True)
        else:
            self._java_tunnel_action_btn.set_visible(True)
            self._java_tunnel_spinner.set_visible(False)
            self._java_tunnel_spinner.set_spinning(False)

        if bedrock_endpoint:
            self._bedrock_tunnel_action_btn.set_label("")
            self._bedrock_tunnel_action_btn.set_icon_name("emblem-system-symbolic")
            self._bedrock_tunnel_action_btn.set_tooltip_text(_("Manage Bedrock tunnel"))
            self._bedrock_tunnel_action_btn.remove_css_class("pill")
            self._bedrock_tunnel_action_btn.add_css_class("flat")
        else:
            self._bedrock_tunnel_action_btn.set_label("")
            self._bedrock_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._bedrock_tunnel_action_btn.set_tooltip_text(_("Add Bedrock tunnel"))
            self._bedrock_tunnel_action_btn.remove_css_class("flat")
            self._bedrock_tunnel_action_btn.add_css_class("flat")

        # Show spinner when in progress, button otherwise
        if self._bedrock_in_progress:
            self._bedrock_tunnel_action_btn.set_visible(False)
            self._bedrock_tunnel_spinner.set_visible(True)
            self._bedrock_tunnel_spinner.set_spinning(True)
        else:
            self._bedrock_tunnel_action_btn.set_visible(True)
            self._bedrock_tunnel_spinner.set_visible(False)
            self._bedrock_tunnel_spinner.set_spinning(False)

        voicechat_endpoint = str(self._cfg.get("voicechat_endpoint", "")).strip()
        if voicechat_endpoint:
            # Format voicechat endpoint with middle dot separator and Port label (domain:port -> domain · Port port)
            if ":" in voicechat_endpoint:
                domain, port = voicechat_endpoint.rsplit(":", 1)
                formatted_endpoint = f"{domain} · Port {port}"
            else:
                formatted_endpoint = voicechat_endpoint
            self._voicechat_domain_row.set_subtitle(formatted_endpoint)
            self._voicechat_domain_row.set_activatable(True)
            self._copy_voicechat_domain_btn.set_sensitive(True)
            self._copy_voicechat_domain_btn.set_visible(True)
        else:
            self._voicechat_domain_row.set_subtitle(_("Not available"))
            self._voicechat_domain_row.set_activatable(True)
            self._copy_voicechat_domain_btn.set_sensitive(False)
            self._copy_voicechat_domain_btn.set_visible(False)

        if voicechat_endpoint:
            self._voicechat_tunnel_action_btn.set_label("")
            self._voicechat_tunnel_action_btn.set_icon_name("emblem-system-symbolic")
            self._voicechat_tunnel_action_btn.set_tooltip_text(_("Manage Voice Chat tunnel"))
            self._voicechat_tunnel_action_btn.remove_css_class("pill")
            self._voicechat_tunnel_action_btn.add_css_class("flat")
        else:
            self._voicechat_tunnel_action_btn.set_label("")
            self._voicechat_tunnel_action_btn.set_icon_name("list-add-symbolic")
            self._voicechat_tunnel_action_btn.set_tooltip_text(_("Add Voice Chat tunnel"))
            self._voicechat_tunnel_action_btn.remove_css_class("flat")
            self._voicechat_tunnel_action_btn.add_css_class("flat")

        # Show spinner when in progress, button otherwise
        if self._voicechat_in_progress:
            self._voicechat_tunnel_action_btn.set_visible(False)
            self._voicechat_tunnel_spinner.set_visible(True)
            self._voicechat_tunnel_spinner.set_spinning(True)
        else:
            self._voicechat_tunnel_action_btn.set_visible(True)
            self._voicechat_tunnel_spinner.set_visible(False)
            self._voicechat_tunnel_spinner.set_spinning(False)

        tunnel_actions_locked = bool(
            self._start_in_progress
            or self._java_tunnel_in_progress
            or self._bedrock_in_progress
            or self._voicechat_in_progress
        )
        self._java_tunnel_action_btn.set_sensitive(not tunnel_actions_locked)
        self._bedrock_tunnel_action_btn.set_sensitive(not tunnel_actions_locked)
        self._voicechat_tunnel_action_btn.set_sensitive(not tunnel_actions_locked)

        if self._start_in_progress:
            self._tunnel_btn.set_label(_("Starting..."))
            self._tunnel_btn.set_sensitive(False)
            self._tunnel_btn.add_css_class("hosty-starting-button")
        elif self._java_tunnel_in_progress or self._bedrock_in_progress or self._voicechat_in_progress:
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
                endpoint = str(playit.get_endpoint_for(self._server_info.id) or "").strip()
        if not endpoint:
            return

        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            clipboard = display.get_clipboard()
            clipboard.set(endpoint)
            self._toast(_("Java tunnel domain copied"))
        except Exception:
            pass

    def _on_java_domain_row_activated(self, *_args):
        endpoint = str(self._cfg.get("java_endpoint", "")).strip()
        if endpoint:
            self._on_copy_tunnel_domain()
            return
        self._on_manage_java_tunnel()

    def _on_copy_bedrock_domain(self, *_args):
        endpoint = str(self._cfg.get("bedrock_endpoint", "")).strip()
        if not endpoint:
            return

        # Extract domain only (remove port if present)
        domain_only = endpoint.rsplit(":", 1)[0] if ":" in endpoint else endpoint

        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            clipboard = display.get_clipboard()
            clipboard.set(domain_only)
            self._toast(_("Bedrock tunnel domain copied"))
        except Exception:
            pass

    def _on_bedrock_domain_row_activated(self, *_args):
        endpoint = str(self._cfg.get("bedrock_endpoint", "")).strip()
        if endpoint:
            self._on_copy_bedrock_domain()
            return
        self._on_manage_bedrock_tunnel()

    def _on_copy_voicechat_domain(self, *_args):
        endpoint = str(self._cfg.get("voicechat_endpoint", "")).strip()
        if not endpoint:
            return

        # Extract domain only (remove port if present)
        domain_only = endpoint.rsplit(":", 1)[0] if ":" in endpoint else endpoint

        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            clipboard = display.get_clipboard()
            clipboard.set(domain_only)
            self._toast(_("Voice Chat tunnel domain copied"))
        except Exception:
            pass

    def _on_voicechat_domain_row_activated(self, *_args):
        endpoint = str(self._cfg.get("voicechat_endpoint", "")).strip()
        if endpoint:
            self._on_copy_voicechat_domain()
            return
        self._on_manage_voicechat_tunnel()

    def _on_tunnel_toggle(self, *_args):
        if not self._server_manager:
            return
        playit = self._server_manager.playit_manager
        if playit.is_running:
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
        self._toast(_("Playit setup completed"))

    def _on_open_dashboard(self, *_args):
        if not _open_uri(PLAYIT_DASHBOARD_URL):
            self._alert(_("Could not open browser"), _("Unable to open playit dashboard."))

    def _confirm_delete_tunnel(self, tunnel_name: str, on_confirm):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete {} tunnel?").format(tunnel_name))
        dialog.set_body(
            _("This will remove the current tunnel domain for this server. You can add a new tunnel again later.")
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "delete":
                on_confirm()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _confirm_regenerate_tunnel(self, tunnel_name: str, on_confirm):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Regenerate {} tunnel?").format(tunnel_name))
        dialog.set_body(
            _(
                "This will replace the current tunnel domain with a new one. "
                "Players using the old domain will no longer be able to connect."
            )
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("regenerate", _("Regenerate"))
        dialog.set_response_appearance("regenerate", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "regenerate":
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
        if self._start_in_progress:
            self._toast(_("Playit startup is already in progress"))
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress:
            self._toast(_("A tunnel operation is already in progress"))
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        had_java_tunnel = bool(str(self._cfg.get("java_endpoint", "")).strip())

        def start_operation():
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
                        java_port = self._server_manager.playit_manager._read_server_port(server_dir)
                        self._propagate_tunnel_endpoint("java_endpoint", java_port, endpoint)
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    else:
                        self._alert(_("Could not update Java tunnel"), msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        if had_java_tunnel:
            server_port = self._server_manager.playit_manager._read_server_port(server_dir)

            def on_java_port_changed(_dialog, new_port):
                if new_port == server_port:
                    return
                old_port = server_port
                self._server_manager.set_java_port(server_id, new_port)
                self._toast(_("Java port changed to {}").format(new_port))
                self._java_tunnel_in_progress = True
                self._refresh_status_row()

                def run():
                    ok, msg, endpoint = self._server_manager.playit_manager.add_java_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                    )

                    if ok and endpoint and not self._has_other_server_with_tunnel_on_port("java_endpoint", old_port):
                        self._server_manager.playit_manager._delete_tunnels_by_port(old_port, "tcp")

                    def ui_done():
                        self._java_tunnel_in_progress = False
                        if ok and endpoint:
                            self._save_server_config({"java_endpoint": endpoint})
                        self._refresh_status_row()
                        if not ok:
                            self._alert(_("Could not create Java tunnel"), msg)

                    GLib.idle_add(ui_done)

                threading.Thread(target=run, daemon=True).start()

            dialog = ManagePlayitTunnelDialog(
                "Java", _("Minecraft Java (TCP)"), server_port, str(self._cfg.get("java_endpoint", "")).strip()
            )
            dialog.connect("regenerate", lambda *_: self._confirm_regenerate_tunnel("Java", start_operation))
            dialog.connect("delete", lambda *_: self._on_delete_java_tunnel())
            dialog.connect("port-changed", on_java_port_changed)
            dialog.present(self.get_root())
            return

        start_operation()

    def _on_manage_bedrock_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if self._start_in_progress:
            self._toast(_("Playit startup is already in progress"))
            return
        if self._java_tunnel_in_progress:
            self._toast(_("Java tunnel operation is already in progress"))
            return
        if self._bedrock_in_progress:
            self._toast(_("Bedrock tunnel creation is already in progress"))
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        had_bedrock_tunnel = bool(str(self._cfg.get("bedrock_endpoint", "")).strip())
        self._maybe_create_bedrock_tunnel(server_id, server_dir, secret, had_bedrock_tunnel)

    def _maybe_create_bedrock_tunnel(self, server_id, server_dir, secret, had_bedrock_tunnel):
        def start_operation():
            self._bedrock_in_progress = True
            self._refresh_status_row()

            def run():
                br_port = int(self._cfg.get("bedrock_port", 19132))
                if had_bedrock_tunnel:
                    ok, msg, endpoint = self._server_manager.playit_manager.regenerate_bedrock_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                        bedrock_port=br_port,
                    )
                else:
                    ok, msg, endpoint = self._server_manager.playit_manager.add_bedrock_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                        bedrock_port=br_port,
                    )

                def ui_done():
                    self._bedrock_in_progress = False
                    if ok and endpoint:
                        self._save_server_config({"bedrock_endpoint": endpoint})
                        self._propagate_tunnel_endpoint("bedrock_endpoint", br_port, endpoint)
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    else:
                        self._alert(_("Could not update Bedrock tunnel"), msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        if had_bedrock_tunnel:
            br_port = int(self._cfg.get("bedrock_port", 19132))

            def on_bedrock_port_changed(_dialog, new_port):
                if new_port == br_port:
                    return
                old_port = br_port
                self._server_manager.set_bedrock_port(server_id, new_port)
                self._cfg["bedrock_port"] = new_port
                self._save_server_config()
                self._server_manager.playit_manager.configure_geyser_mod(server_dir, new_port)
                self._toast(_("Bedrock port changed to {}").format(new_port))
                self._bedrock_in_progress = True
                self._refresh_status_row()

                def run():
                    ok, msg, endpoint = self._server_manager.playit_manager.add_bedrock_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                        bedrock_port=new_port,
                    )

                    if ok and endpoint and not self._has_other_server_with_tunnel_on_port("bedrock_endpoint", old_port):
                        self._server_manager.playit_manager._delete_tunnels_by_port(old_port, "udp")

                    def ui_done():
                        self._bedrock_in_progress = False
                        if ok and endpoint:
                            self._save_server_config({"bedrock_endpoint": endpoint})
                        self._refresh_status_row()
                        if not ok:
                            self._alert(_("Could not create Bedrock tunnel"), msg)

                    GLib.idle_add(ui_done)

                threading.Thread(target=run, daemon=True).start()

            dialog = ManagePlayitTunnelDialog(
                "Bedrock", _("Minecraft Bedrock (UDP)"), br_port, str(self._cfg.get("bedrock_endpoint", "")).strip()
            )
            dialog.connect("regenerate", lambda *_: self._confirm_regenerate_tunnel("Bedrock", start_operation))
            dialog.connect("delete", lambda *_: self._on_delete_bedrock_tunnel())
            dialog.connect("port-changed", on_bedrock_port_changed)
            dialog.present(self.get_root())
            return

        if self._has_mod_installed(server_dir, "geyser") and self._has_mod_installed(server_dir, "floodgate"):
            start_operation()
        else:
            self._confirm_required_mod_install(
                "Bedrock",
                [
                    ("geyser", "Geyser"),
                    ("floodgate", "Floodgate"),
                ],
                start_operation,
            )

    def _on_manage_voicechat_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if self._start_in_progress:
            self._toast(_("Playit startup is already in progress"))
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress:
            self._toast(_("A tunnel operation is already in progress"))
            return
        if self._voicechat_in_progress:
            self._toast(_("Voice Chat tunnel creation is already in progress"))
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        had_voicechat_tunnel = bool(str(self._cfg.get("voicechat_endpoint", "")).strip())

        self._maybe_create_voicechat_tunnel(server_id, server_dir, secret, had_voicechat_tunnel)

    def _maybe_create_voicechat_tunnel(self, server_id, server_dir, secret, had_voicechat_tunnel):
        def start_operation():
            self._voicechat_in_progress = True
            self._refresh_status_row()

            def run():
                vc_port = int(self._cfg.get("voicechat_port", 24454))
                if had_voicechat_tunnel:
                    ok, msg, endpoint = self._server_manager.playit_manager.regenerate_voicechat_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                        voicechat_port=vc_port,
                    )
                else:
                    ok, msg, endpoint = self._server_manager.playit_manager.add_voicechat_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                        voicechat_port=vc_port,
                    )

                def ui_done():
                    self._voicechat_in_progress = False
                    if ok and endpoint:
                        self._save_server_config({"voicechat_endpoint": endpoint})
                        self._propagate_tunnel_endpoint("voicechat_endpoint", vc_port, endpoint)
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    else:
                        self._alert(_("Could not update Voice Chat tunnel"), msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        if had_voicechat_tunnel:
            vc_port = int(self._cfg.get("voicechat_port", 24454))

            def on_voicechat_port_changed(_dialog, new_port):
                if new_port == vc_port:
                    return
                old_port = vc_port
                self._server_manager.set_voicechat_port(server_id, new_port)
                self._cfg["voicechat_port"] = new_port
                self._save_server_config()
                self._server_manager.playit_manager.configure_voicechat_mod(
                    server_dir,
                    server_id,
                    voicechat_port=new_port,
                )
                self._toast(_("Voice Chat port changed to {}").format(new_port))
                self._voicechat_in_progress = True
                self._refresh_status_row()

                def run():
                    ok, msg, endpoint = self._server_manager.playit_manager.add_voicechat_tunnel(
                        server_id,
                        server_dir,
                        secret=secret,
                        auto_install=True,
                        voicechat_port=new_port,
                    )

                    if (
                        ok
                        and endpoint
                        and not self._has_other_server_with_tunnel_on_port("voicechat_endpoint", old_port)
                    ):
                        self._server_manager.playit_manager._delete_tunnels_by_port(old_port, "udp")

                    def ui_done():
                        self._voicechat_in_progress = False
                        if ok and endpoint:
                            self._save_server_config({"voicechat_endpoint": endpoint})
                        self._refresh_status_row()
                        if not ok:
                            self._alert(_("Could not create Voice Chat tunnel"), msg)

                    GLib.idle_add(ui_done)

                threading.Thread(target=run, daemon=True).start()

            dialog = ManagePlayitTunnelDialog(
                "Voice Chat",
                _("Simple Voice Chat (UDP)"),
                vc_port,
                str(self._cfg.get("voicechat_endpoint", "")).strip(),
            )
            dialog.connect("regenerate", lambda *_: self._confirm_regenerate_tunnel("Voice Chat", start_operation))
            dialog.connect("delete", lambda *_: self._on_delete_voicechat_tunnel())
            dialog.connect("port-changed", on_voicechat_port_changed)
            dialog.present(self.get_root())
            return

        if self._has_mod_installed(server_dir, "voice-chat", "simple-voice-chat"):
            start_operation()
        else:
            self._confirm_required_mod_install(
                "Voice Chat",
                [("simple-voice-chat", "Simple Voice Chat")],
                start_operation,
            )

    def _on_delete_java_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if (
            self._java_tunnel_in_progress
            or self._bedrock_in_progress
            or self._voicechat_in_progress
            or self._start_in_progress
        ):
            self._toast(_("A tunnel operation is already in progress"))
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
                        java_port = self._server_manager.playit_manager._read_server_port(server_dir)
                        self._propagate_tunnel_endpoint("java_endpoint", java_port, "")
                        self._load_server_config()
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    elif "No java tunnel found" in str(msg):
                        self._toast(_("Java tunnel already missing"))
                    else:
                        self._alert(_("Could not delete Java tunnel"), msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        self._confirm_delete_tunnel("Java", confirmed_delete)

    def _on_delete_bedrock_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if (
            self._java_tunnel_in_progress
            or self._bedrock_in_progress
            or self._voicechat_in_progress
            or self._start_in_progress
        ):
            self._toast(_("A tunnel operation is already in progress"))
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
                        br_port = self._server_manager.get_bedrock_port(self._server_info.id)
                        self._propagate_tunnel_endpoint("bedrock_endpoint", br_port, "")
                        self._load_server_config()
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    elif "No bedrock tunnel found" in str(msg):
                        self._toast(_("Bedrock tunnel already missing"))
                    else:
                        self._alert(_("Could not delete Bedrock tunnel"), msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        self._confirm_delete_tunnel("Bedrock", confirmed_delete)

    def _on_delete_voicechat_tunnel(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if (
            self._java_tunnel_in_progress
            or self._bedrock_in_progress
            or self._voicechat_in_progress
            or self._start_in_progress
        ):
            self._toast(_("A tunnel operation is already in progress"))
            return

        def confirmed_delete():
            self._voicechat_in_progress = True
            self._refresh_status_row()
            server_dir = str(self._server_info.server_dir)
            secret = str(self._cfg.get("secret", "")).strip()

            def run():
                ok, msg = self._server_manager.playit_manager.delete_voicechat_tunnel(
                    server_dir,
                    secret=secret,
                    auto_install=True,
                )

                def ui_done():
                    self._voicechat_in_progress = False
                    if ok or "No voice chat tunnel found" in str(msg):
                        self._save_server_config({"voicechat_endpoint": ""})
                        vc_port = self._server_manager.get_voicechat_port(self._server_info.id)
                        self._propagate_tunnel_endpoint("voicechat_endpoint", vc_port, "")
                        self._load_server_config()
                    self._refresh_status_row()
                    if ok:
                        self._toast(msg)
                    elif "No voice chat tunnel found" in str(msg):
                        self._toast(_("Voice Chat tunnel already missing"))
                    else:
                        self._alert(_("Could not delete Voice Chat tunnel"), msg)

                GLib.idle_add(ui_done)

            threading.Thread(target=run, daemon=True).start()

        self._confirm_delete_tunnel("Voice Chat", confirmed_delete)

    def _has_mod_installed(self, server_dir: str, *mod_patterns: str) -> bool:
        """Check if any of the given mod patterns are installed in the server.

        Args:
            server_dir: The server directory path
            *mod_patterns: One or more mod name patterns to search for (case-insensitive, no extension)

        Returns:
            True if any mod matching the patterns is found, False otherwise
        """
        mods_dir = Path(server_dir) / "mods"
        if not mods_dir.exists():
            return False

        # Get all jar files in mods directory
        installed_mods = {f.stem.lower() for f in mods_dir.glob("*.jar")}

        # Check if any of the patterns match an installed mod
        for pattern in mod_patterns:
            pattern_lower = pattern.lower()
            # Normalize pattern: remove hyphens for matching
            pattern_normalized = pattern_lower.replace("-", "")

            for mod_name in installed_mods:
                # Check if pattern is in mod name or mod name starts with pattern
                if pattern_lower in mod_name or mod_name.startswith(pattern_lower):
                    return True
                # Also check normalized version (e.g., "voicechat" matches "voice-chat")
                mod_normalized = mod_name.replace("-", "")
                if pattern_normalized in mod_normalized or mod_normalized.startswith(pattern_normalized):
                    return True

        return False

    def _exact_compatible_modrinth_version(self, project_id: str):
        if not self._server_info:
            return None
        from hosty.shared.backend import modrinth_client

        mc_version = str(self._server_info.mc_version or "").strip()
        if not mc_version:
            return None
        versions = modrinth_client.get_project_versions(project_id)
        for version in versions:
            loaders = [str(loader).lower() for loader in (version.loaders or [])]
            if mc_version in (version.game_versions or []) and "fabric" in loaders:
                return version
        return None

    def _record_tunnel_installed_mod(self, project_id: str, title: str, version) -> None:
        if not self._server_info:
            return
        state_path = self._server_info.server_dir / ".hosty-mod-installs.json"
        try:
            data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            mods = data.get("mods") if isinstance(data.get("mods"), dict) else {}
            mods[str(project_id)] = {
                "title": str(title),
                "version_id": str(version.version_id),
                "version_number": str(version.version_number),
                "filename": str(version.filename),
            }
            state_path.write_text(json.dumps({"mods": mods}, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _record_tunnel_dependency_installs(self, parent_filename: str, dep_versions: list) -> None:
        if not self._server_info:
            return
        parent_key = str(parent_filename or "").strip().lower()
        if not parent_key:
            return
        state_path = self._server_info.server_dir / ".hosty-mod-dependencies.json"
        try:
            data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            req = data.get("required_by") if isinstance(data.get("required_by"), dict) else {}
            for dep in dep_versions:
                dep_key = str(getattr(dep, "filename", "") or "").strip().lower()
                if not dep_key or dep_key == parent_key:
                    continue
                parents = set(req.get(dep_key, []))
                parents.add(parent_key)
                req[dep_key] = sorted(parents)

                dep_project_id = str(getattr(dep, "project_id", "") or "").strip()
                dep_title = str(
                    getattr(dep, "title", "") or getattr(dep, "name", "") or dep_project_id or dep_key
                ).strip()
                dep_filename = str(getattr(dep, "filename", "") or "").strip()
                if dep_project_id and dep_filename:
                    self._record_tunnel_installed_mod(
                        dep_project_id,
                        dep_title or dep_project_id,
                        dep,
                    )
            state_path.write_text(json.dumps({"required_by": req}, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _install_tunnel_mod(self, project_id: str, title: str) -> tuple[bool, str]:
        if not self._server_info:
            return False, _("No server selected")
        from hosty.shared.backend import modrinth_client

        version = self._exact_compatible_modrinth_version(project_id)
        if not version:
            return False, _("{} is not available for Minecraft {}").format(title, self._server_info.mc_version)

        mods_dir = self._server_info.server_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        installed_names = {path.name.lower() for path in mods_dir.glob("*.jar")}

        deps = modrinth_client.resolve_required_dependencies(
            version.version_id,
            self._server_info.mc_version,
            "fabric",
        )
        for dep in deps:
            dep_name = str(dep.filename).strip()
            if not dep_name or dep_name.lower() in installed_names:
                continue
            if dep_name.lower() == str(version.filename).lower():
                continue
            modrinth_client.download_to(dep.download_url, mods_dir / dep_name)
            installed_names.add(dep_name.lower())

        if str(version.filename).lower() not in installed_names:
            modrinth_client.download_to(version.download_url, mods_dir / version.filename)

        self._record_tunnel_installed_mod(version.project_id or project_id, title, version)
        self._record_tunnel_dependency_installs(version.filename, deps)

        playit = self._server_manager.playit_manager
        if project_id == "geyser":
            playit.configure_geyser_mod(str(self._server_info.server_dir))
        elif project_id == "floodgate":
            playit.configure_floodgate_mod(str(self._server_info.server_dir))
        elif project_id == "simple-voice-chat":
            from hosty.shared.backend.playit_config import load_playit_config

            vc_cfg = load_playit_config(self._server_info.server_dir)
            vc_port = int(vc_cfg.get("voicechat_port", 24454))
            playit.configure_voicechat_mod(
                str(self._server_info.server_dir),
                self._server_info.id,
                endpoint=str(vc_cfg.get("voicechat_endpoint", "")).strip(),
                voicechat_port=vc_port,
            )

        return True, _("Installed {}").format(title)

    def _confirm_required_mod_install(self, tunnel_name: str, mods: list[tuple[str, str]], on_confirm):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Add {} tunnel?").format(tunnel_name))
        names = ", ".join(title for _project_id, title in mods)
        dialog.set_body(
            _("This tunnel needs {}. Hosty can install compatible Fabric versions automatically.").format(names)
        )

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install Mods"))

        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "install":
                return

            if tunnel_name == "Bedrock":
                self._bedrock_in_progress = True
            elif tunnel_name == "Voice Chat":
                self._voicechat_in_progress = True
            self._refresh_status_row()
            self._toast(_("Installing {} mod support...").format(tunnel_name))

            def worker():
                warnings: list[str] = []
                installed: list[str] = []
                for project_id, title in mods:
                    if self._has_mod_installed(str(self._server_info.server_dir), project_id):
                        continue
                    try:
                        ok, msg = self._install_tunnel_mod(project_id, title)
                    except Exception as exc:
                        ok, msg = False, _("Could not install {}: {}").format(title, exc)
                    if ok:
                        installed.append(title)
                    else:
                        warnings.append(msg)

                def done():
                    if tunnel_name == "Bedrock":
                        self._bedrock_in_progress = False
                    elif tunnel_name == "Voice Chat":
                        self._voicechat_in_progress = False
                    self._refresh_status_row()
                    if installed:
                        self._toast(_("Installed {}").format(", ".join(installed)))
                    if warnings:
                        warn = Adw.AlertDialog()
                        warn.set_heading(_("Some mods were not installed"))
                        warn.set_body(_("{}\n\nHosty will create the tunnel anyway.").format("\n".join(warnings)))
                        warn.add_response("ok", _("OK"))
                        warn.set_default_response("ok")
                        warn.set_close_response("ok")
                        warn.connect("response", lambda *_: on_confirm())
                        warn.present(self.get_root())
                    else:
                        on_confirm()
                    return False

                GLib.idle_add(done)

            threading.Thread(target=worker, daemon=True).start()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _on_start(self, *_args):
        if not self._server_manager or not self._server_info:
            return
        if not self._is_setup_complete():
            self._on_open_setup_dialog()
            return
        if self._java_tunnel_in_progress or self._bedrock_in_progress or self._voicechat_in_progress:
            self._toast(_("A tunnel operation is already in progress"))
            return
        if self._start_in_progress:
            self._refresh_status_row()
            self._toast(_("Playit startup is already in progress"))
            return

        self._save_server_config()
        server_id = self._server_info.id
        server_dir = str(self._server_info.server_dir)
        secret = str(self._cfg.get("secret", "")).strip()
        self._start_in_progress = True
        self._refresh_status_row()

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
            if ok:
                cfg = load_playit_config(server_dir)
                br_port = int(cfg.get("bedrock_port", 19132))
                vc_port = int(cfg.get("voicechat_port", 24454))
                playit = self._server_manager.playit_manager
                playit.verify_playit_mod_configs(
                    server_dir,
                    server_id,
                    bedrock_endpoint=str(cfg.get("bedrock_endpoint", "")).strip(),
                    voicechat_endpoint=str(cfg.get("voicechat_endpoint", "")).strip(),
                    bedrock_port=br_port,
                    voicechat_port=vc_port,
                )

            def ui_done():
                self._start_in_progress = False
                self._refresh_status_row()
                if ok:
                    self._toast(_("Playit agent started"))
                else:
                    self._alert(_("Could not start playit"), msg)

            GLib.idle_add(ui_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_stop(self, *_args):
        if not self._server_manager:
            return

        ok, msg = self._server_manager.playit_manager.stop()
        self._refresh_status_row()
        if ok:
            self._toast(_("Playit agent stopped"))
        else:
            self._alert(_("Could not stop playit"), msg)
