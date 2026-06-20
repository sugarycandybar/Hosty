"""
ConnectView - Server connection tools (playit.gg tunnel).
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gtk

from hosty.shared.backend.server_manager import ServerInfo, ServerManager

PLAYIT_DASHBOARD_URL = "https://playit.gg/account/tunnels"


from .mixins import LocalIpMixin, PlayersMixin, PlayitMixin
from .utils import *


class ConnectView(Gtk.Box, LocalIpMixin, PlayersMixin, PlayitMixin):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._server_info: ServerInfo | None = None
        self._server_manager: ServerManager | None = None
        self._status_handler_id: int | None = None
        self._endpoint_handler_id: int | None = None
        self._manager_changed_id: int | None = None
        self._cfg = {}
        self._suppress_config_updates = False
        self._start_in_progress = False
        self._java_tunnel_in_progress = False
        self._bedrock_in_progress = False
        self._voicechat_in_progress = False
        self._local_ip_rows: list[Adw.ActionRow] = []
        self._local_ip_value = _("Not available")
        self._whitelist_status_rows: list[Adw.ActionRow] = []
        self._whitelist_toggle_rows: list[Adw.SwitchRow] = []
        self._suppress_whitelist_toggle = False
        self._whitelist_groups: list[Adw.PreferencesGroup] = []
        self._banned_groups: list[Adw.PreferencesGroup] = []
        self._player_rows_by_group: dict[Gtk.Widget, list[Gtk.Widget]] = {}
        self._whitelist_list_rows: list[Adw.ExpanderRow] = []
        self._banned_list_rows: list[Adw.ExpanderRow] = []

        self._banner = Adw.Banner()
        self._banner.set_title(_("Restart the server to apply changes"))
        self._banner.set_button_label(_("Dismiss"))
        self._banner.set_revealed(False)
        self._banner.connect("button-clicked", lambda b: b.set_revealed(False))
        self.append(self._banner)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._mode_stack = Gtk.Stack()
        self._mode_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._mode_stack.set_transition_duration(180)

        self._mode_stack.add_named(self._build_setup_required_page(), "setup")
        self._mode_stack.add_named(self._build_ready_page(), "ready")

        scrolled.set_child(self._mode_stack)
        self.append(scrolled)

        self._refresh_local_ip_row()

    def _build_setup_required_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        page.add(self._make_local_network_group())

        setup_group = Adw.PreferencesGroup(
            title=_("Playit.gg"),
            description=_("Set up Playit to expose your server publicly"),
        )
        setup_row = Adw.ActionRow(
            title=_("Playit setup required"),
            subtitle=_("Install and claim Playit for this server"),
        )

        self._setup_btn = Gtk.Button(label=_("Set Up Playit"), valign=Gtk.Align.CENTER)
        self._setup_btn.add_css_class("suggested-action")
        self._setup_btn.set_valign(Gtk.Align.CENTER)
        self._setup_btn.connect("clicked", self._on_open_setup_dialog)
        setup_row.add_suffix(self._setup_btn)
        setup_group.add(setup_row)

        page.add(setup_group)
        self._append_players_groups(page)

        return page

    def _build_ready_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        page.add(self._make_local_network_group())

        group = Adw.PreferencesGroup(
            title=_("Playit.gg"),
            description=_("Share with friends"),
        )

        self._tunnel_row = Adw.ActionRow(title=_("Agent"), subtitle=_("Stopped"))
        self._tunnel_row.set_activatable(False)
        self._tunnel_btn = Gtk.Button(label=_("Start Agent"), valign=Gtk.Align.CENTER)
        self._tunnel_btn.add_css_class("suggested-action")
        self._tunnel_btn.add_css_class("playit-compact-button")
        self._tunnel_btn.connect("clicked", self._on_tunnel_toggle)
        self._tunnel_row.add_suffix(self._tunnel_btn)
        group.add(self._tunnel_row)

        self._tunnel_domain_row = Adw.ActionRow(title=_("Java tunnel domain"), subtitle=_("Not available"))
        self._tunnel_domain_row.set_activatable(False)
        self._tunnel_domain_row.connect("activated", self._on_java_domain_row_activated)
        self._copy_tunnel_domain_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        self._copy_tunnel_domain_btn.add_css_class("flat")
        self._copy_tunnel_domain_btn.set_tooltip_text(_("Copy Java tunnel domain"))
        self._copy_tunnel_domain_btn.set_sensitive(False)
        self._copy_tunnel_domain_btn.set_visible(False)
        self._copy_tunnel_domain_btn.connect("clicked", self._on_copy_tunnel_domain)
        self._tunnel_domain_row.add_suffix(self._copy_tunnel_domain_btn)
        self._java_tunnel_action_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        self._java_tunnel_action_btn.add_css_class("flat")
        self._java_tunnel_action_btn.set_tooltip_text(_("Add Java tunnel"))
        self._java_tunnel_action_btn.connect("clicked", self._on_manage_java_tunnel)
        self._java_tunnel_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self._java_tunnel_spinner.set_visible(False)
        self._java_tunnel_spinner.set_spinning(False)
        self._java_tunnel_spinner.set_size_request(24, 24)
        self._tunnel_domain_row.add_suffix(self._java_tunnel_spinner)
        self._tunnel_domain_row.add_suffix(self._java_tunnel_action_btn)
        group.add(self._tunnel_domain_row)

        self._bedrock_domain_row = Adw.ActionRow(title=_("Bedrock tunnel domain"), subtitle=_("Not available"))
        self._bedrock_domain_row.set_activatable(False)
        self._bedrock_domain_row.connect("activated", self._on_bedrock_domain_row_activated)
        self._copy_bedrock_domain_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        self._copy_bedrock_domain_btn.add_css_class("flat")
        self._copy_bedrock_domain_btn.set_tooltip_text(_("Copy bedrock tunnel domain"))
        self._copy_bedrock_domain_btn.set_sensitive(False)
        self._copy_bedrock_domain_btn.set_visible(False)
        self._copy_bedrock_domain_btn.connect("clicked", self._on_copy_bedrock_domain)
        self._bedrock_domain_row.add_suffix(self._copy_bedrock_domain_btn)
        self._bedrock_tunnel_action_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        self._bedrock_tunnel_action_btn.add_css_class("flat")
        self._bedrock_tunnel_action_btn.set_tooltip_text(_("Add Bedrock tunnel"))
        self._bedrock_tunnel_action_btn.connect("clicked", self._on_manage_bedrock_tunnel)
        self._bedrock_tunnel_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self._bedrock_tunnel_spinner.set_visible(False)
        self._bedrock_tunnel_spinner.set_spinning(False)
        self._bedrock_tunnel_spinner.set_size_request(24, 24)
        self._bedrock_domain_row.add_suffix(self._bedrock_tunnel_spinner)
        self._bedrock_domain_row.add_suffix(self._bedrock_tunnel_action_btn)
        group.add(self._bedrock_domain_row)

        self._voicechat_domain_row = Adw.ActionRow(title=_("Voice Chat tunnel domain"), subtitle=_("Not available"))
        self._voicechat_domain_row.set_activatable(False)
        self._voicechat_domain_row.connect("activated", self._on_voicechat_domain_row_activated)
        self._copy_voicechat_domain_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        self._copy_voicechat_domain_btn.add_css_class("flat")
        self._copy_voicechat_domain_btn.set_tooltip_text(_("Copy voice chat tunnel domain"))
        self._copy_voicechat_domain_btn.set_sensitive(False)
        self._copy_voicechat_domain_btn.set_visible(False)
        self._copy_voicechat_domain_btn.connect("clicked", self._on_copy_voicechat_domain)
        self._voicechat_domain_row.add_suffix(self._copy_voicechat_domain_btn)
        self._voicechat_tunnel_action_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        self._voicechat_tunnel_action_btn.add_css_class("flat")
        self._voicechat_tunnel_action_btn.set_tooltip_text(_("Add Voice Chat tunnel"))
        self._voicechat_tunnel_action_btn.connect("clicked", self._on_manage_voicechat_tunnel)
        self._voicechat_tunnel_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self._voicechat_tunnel_spinner.set_visible(False)
        self._voicechat_tunnel_spinner.set_spinning(False)
        self._voicechat_tunnel_spinner.set_size_request(24, 24)
        self._voicechat_domain_row.add_suffix(self._voicechat_tunnel_spinner)
        self._voicechat_domain_row.add_suffix(self._voicechat_tunnel_action_btn)
        group.add(self._voicechat_domain_row)

        settings_row = Adw.ExpanderRow(
            title=_("Playit settings"),
        )

        self._auto_start_row = Adw.SwitchRow(
            title=_("Start with server"),
            subtitle=_("Automatically start and stop with this server"),
        )
        self._auto_start_row.connect("notify::active", self._on_auto_start_toggled)
        settings_row.add_row(self._auto_start_row)

        dashboard_row = Adw.ActionRow(title=_("Open playit dashboard"))
        dashboard_row.add_prefix(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        dashboard_row.set_activatable(True)
        dashboard_row.connect("activated", self._on_open_dashboard)
        settings_row.add_row(dashboard_row)

        reset_row = Adw.ActionRow(title=_("Set Up Playit Again"))
        reset_row.add_prefix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        reset_row.set_activatable(True)
        reset_row.connect("activated", self._on_open_setup_dialog)
        settings_row.add_row(reset_row)

        group.add(settings_row)

        page.add(group)
        self._append_players_groups(page)
        return page

    def set_server(self, server_info: ServerInfo, server_manager: ServerManager):
        if self._server_manager and self._manager_changed_id is not None:
            try:
                self._server_manager.disconnect(self._manager_changed_id)
            except Exception:
                pass
            self._manager_changed_id = None

        self._server_info = server_info
        self._server_manager = server_manager

        playit = self._server_manager.playit_manager
        if self._status_handler_id is not None:
            try:
                playit.disconnect(self._status_handler_id)
            except Exception:
                pass
            self._status_handler_id = None
        if self._endpoint_handler_id is not None:
            try:
                playit.disconnect(self._endpoint_handler_id)
            except Exception:
                pass
            self._endpoint_handler_id = None

        self._status_handler_id = playit.connect("status-changed", self._on_playit_status_changed)
        self._endpoint_handler_id = playit.connect("endpoint-changed", self._on_playit_endpoint_changed)
        self._manager_changed_id = self._server_manager.connect("server-changed", self._on_server_changed)
        self._refresh_local_ip_row()
        self._load_server_config()
        self._banner.set_revealed(False)
        self._refresh_whitelist_status()
        self._refresh_player_lists()
        self._refresh_mode()
        self._refresh_status_row()

    def _on_server_changed(self, _manager, server_id):
        if not self._server_info or server_id != self._server_info.id:
            return
        self._refresh_whitelist_status()
        self._refresh_player_lists()

    def _server_dir(self) -> Path | None:
        if not self._server_info:
            return None
        return Path(self._server_info.server_dir)

    def _refresh_whitelist_status(self):
        enabled = False
        if self._server_manager and self._server_info:
            cfg = self._server_manager.get_config(self._server_info.id)
            if cfg:
                cfg.load()
                enabled = cfg.get_bool("white-list", False)

        self._suppress_whitelist_toggle = True
        for row in self._whitelist_toggle_rows:
            row.set_active(enabled)
        self._suppress_whitelist_toggle = False

    def _on_whitelist_toggled(self, row, _pspec):
        if self._suppress_whitelist_toggle:
            return
        if not self._server_manager or not self._server_info:
            return

        cfg = self._server_manager.get_config(self._server_info.id)
        if cfg:
            cfg.load()
            cfg.set_value("white-list", row.get_active())
            cfg.save()

        process = self._server_manager.get_process(self._server_info.id)
        self._banner.set_revealed(bool(process and process.is_running))

        self._server_manager.emit_on_main_thread("server-changed", self._server_info.id)

    def _server_running(self) -> bool:
        if not self._server_manager or not self._server_info:
            return False
        process = self._server_manager.get_process(self._server_info.id)
        return bool(process and process.is_running)

    def _alert(self, title: str, body: str):
        d = Adw.AlertDialog()
        d.set_heading(title)
        d.set_body(body)
        d.add_response("ok", _("OK"))
        import re

        urls = re.findall(r"https?://[^\s]+", body)
        if urls:
            url = urls[0]
            label = Gtk.Label(
                label=f'<a href="{url}">Upgrade your account</a>',
                use_markup=True,
                halign=Gtk.Align.CENTER,
                margin_top=8,
            )
            label.connect("activate-link", lambda _l, uri: Gtk.show_uri(self.get_root(), uri, Gdk.CURRENT_TIME) or True)
            d.set_extra_child(label)
        d.present(self.get_root())

    def _toast(
        self,
        message: str,
        button_label: str | None = None,
        on_button=None,
        timeout: int = 3,
    ):
        root = self.get_root()
        if root and hasattr(root, "show_toast"):
            root.show_toast(
                message,
                button_label=button_label,
                on_button=on_button,
                timeout=timeout,
            )
