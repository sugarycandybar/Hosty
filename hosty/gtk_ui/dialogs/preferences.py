"""
Application preferences window
"""

from __future__ import annotations

import os
import secrets
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from hosty.daemon.host import load_daemon_config, save_daemon_config
from hosty.i18n import LANGUAGES
from hosty.i18n import set_language as set_app_language
from hosty.shared.backend.preferences_manager import PreferencesManager
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.utils.constants import DATA_DIR


def _open_data_folder() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        os.startfile(str(DATA_DIR))
        return

    Gio.AppInfo.launch_default_for_uri(DATA_DIR.as_uri())


def show_preferences_window(
    parent: Gtk.Window,
    preferences: PreferencesManager,
    server_manager: ServerManager | None = None,
    application=None,
):
    win = Adw.PreferencesDialog()

    def show_pref_toast(message: str) -> None:
        win.add_toast(Adw.Toast(title=message))

    page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")

    # ---------- Application ----------
    app_group = Adw.PreferencesGroup(
        title=_("Application"),
    )
    data_row = Adw.ActionRow(title=_("Data folder"), subtitle=str(DATA_DIR))
    try:
        data_row.set_subtitle_lines(2)
    except Exception:
        pass
    data_button = Gtk.Button(valign=Gtk.Align.CENTER)
    data_image = Gtk.Image.new_from_icon_name("folder-open-symbolic")
    data_button.set_child(data_image)
    data_button.connect("clicked", lambda _: _open_data_folder())
    data_row.add_suffix(data_button)
    app_group.add(data_row)

    bg_expander = Adw.ExpanderRow(
        title=_("Run in background"),
    )
    bg_expander.set_show_enable_switch(True)
    bg_expander.set_enable_expansion(preferences.run_in_background_on_close)

    startup_row = Adw.SwitchRow(
        title=_("Open Hosty on startup"),
    )
    startup_row.set_active(preferences.open_on_startup)
    startup_row.set_sensitive(preferences.run_in_background_on_close)
    bg_expander.add_row(startup_row)

    def on_bg_toggled(row, _pspec):
        active = row.get_enable_expansion()
        preferences.run_in_background_on_close = active
        startup_row.set_sensitive(active)

        if not active and startup_row.get_active():
            startup_row.set_active(False)

        if active:
            # If turning on background
            from hosty.shared.utils.portal import request_background

            def on_bg_response(success, bg, auto, err):
                if not success or not bg:
                    GLib.idle_add(row.set_enable_expansion, False)
                    GLib.idle_add(preferences.__setattr__, "run_in_background_on_close", False)

            request_background(False, on_bg_response)

    bg_expander.connect("notify::enable-expansion", on_bg_toggled)

    def on_startup_toggled(row, _pspec):
        active = row.get_active()
        preferences.open_on_startup = active

        from hosty.shared.utils.portal import request_background

        def on_start_response(success, bg, auto, err):
            if active and (not success or not auto):
                GLib.idle_add(row.set_active, False)
                GLib.idle_add(preferences.__setattr__, "open_on_startup", False)

        request_background(active, on_start_response)

    startup_row.connect("notify::active", on_startup_toggled)

    app_group.add(bg_expander)

    prevent_sleep_row = Adw.SwitchRow(
        title=_("Prevent sleep while server is running"),
    )
    prevent_sleep_row.set_active(preferences.prevent_sleep_while_running)

    def on_prevent_sleep_toggled(row, _pspec):
        preferences.prevent_sleep_while_running = row.get_active()
        try:
            if hasattr(parent, "_update_sleep_inhibit"):
                parent._update_sleep_inhibit()
        except Exception:
            pass

    prevent_sleep_row.connect("notify::active", on_prevent_sleep_toggled)
    app_group.add(prevent_sleep_row)

    page.add(app_group)

    # ---------- Appearance ----------
    # Theme lives in the main (hamburger) menu as the Ptyxis-style
    # three-circle selector, not here.
    appearance_group = Adw.PreferencesGroup(
        title=_("Appearance"),
    )

    lang_keys = list(LANGUAGES.keys())
    lang_names = list(LANGUAGES.values())
    language_model = Gtk.StringList.new(lang_names)

    lang_row = Adw.ComboRow(
        title=_("Language"),
        model=language_model,
    )
    current_lang = preferences.language
    lang_row.set_selected(lang_keys.index(current_lang) if current_lang in lang_keys else 0)

    def on_language_changed(row, _pspec):
        selected = row.get_selected()
        lang_code = lang_keys[selected]
        if lang_code == preferences.language:
            return
        preferences.language = lang_code
        set_app_language(lang_code)
        show_pref_toast(_("Restart required to apply language change"))

    lang_row.connect("notify::selected", on_language_changed)
    appearance_group.add(lang_row)

    page.add(appearance_group)

    # ---------- Backups ----------
    backups_group = Adw.PreferencesGroup(
        title=_("Backups"),
    )

    scope_keys = ["world", "full"]
    scope_names = [_("Worlds only"), _("Everything")]

    backup_expander = Adw.ExpanderRow(
        title=_("Auto backup on stop"),
    )
    backup_expander.set_show_enable_switch(True)
    backup_expander.set_enable_expansion(preferences.auto_backup_on_stop)

    def _backup_scope_subtitle() -> str:
        if not preferences.auto_backup_on_stop:
            return _("Off")
        scope = preferences.auto_backup_scope
        return scope_names[scope_keys.index(scope)] if scope in scope_keys else scope_names[0]

    backup_expander.set_subtitle(_backup_scope_subtitle())

    scope_row = Adw.ComboRow(
        title=_("Back up"),
        model=Gtk.StringList.new(scope_names),
    )
    current_scope = preferences.auto_backup_scope
    scope_row.set_selected(scope_keys.index(current_scope) if current_scope in scope_keys else 0)
    scope_row.set_sensitive(preferences.auto_backup_on_stop)
    backup_expander.add_row(scope_row)

    def on_backup_scope_changed(row, _pspec):
        preferences.auto_backup_scope = scope_keys[row.get_selected()]
        backup_expander.set_subtitle(_backup_scope_subtitle())

    scope_row.connect("notify::selected", on_backup_scope_changed)

    def on_autobackup_toggled(row, _pspec):
        active = row.get_enable_expansion()
        preferences.auto_backup_on_stop = active
        scope_row.set_sensitive(active)
        backup_expander.set_subtitle(_backup_scope_subtitle())

    backup_expander.connect("notify::enable-expansion", on_autobackup_toggled)
    backups_group.add(backup_expander)

    from hosty.shared.backend.preferences_manager import BACKUP_RETENTION_OPTIONS

    retention_values = list(BACKUP_RETENTION_OPTIONS)
    retention_labels = [_("Never") if days == 0 else _("{} days").format(days) for days in retention_values]
    retention_row = Adw.ComboRow(
        title=_("Delete backups older than"),
        model=Gtk.StringList.new(retention_labels),
    )
    current_retention = preferences.backup_retention_days
    retention_row.set_selected(
        retention_values.index(current_retention) if current_retention in retention_values else 0
    )

    def on_retention_changed(row, _pspec):
        preferences.backup_retention_days = retention_values[row.get_selected()]

    retention_row.connect("notify::selected", on_retention_changed)
    backups_group.add(retention_row)

    from hosty.shared.backend.preferences_manager import MAX_BACKUPS_LIMIT

    max_backups_row = Adw.SpinRow(
        title=_("Maximum backups per server"),
        subtitle=_("Oldest are deleted first (0 keeps all)"),
        adjustment=Gtk.Adjustment(
            value=preferences.max_backups,
            lower=0,
            upper=MAX_BACKUPS_LIMIT,
            step_increment=1,
            page_increment=10,
        ),
    )

    def on_max_backups_changed(row, _pspec):
        preferences.max_backups = int(row.get_value())

    max_backups_row.connect("notify::value", on_max_backups_changed)
    backups_group.add(max_backups_row)

    page.add(backups_group)

    # ---------- Mods ----------
    mods_group = Adw.PreferencesGroup(
        title=_("Mods"),
    )

    dep_row = Adw.SwitchRow(
        title=_("Auto resolve mod dependencies"),
    )
    dep_row.set_active(preferences.auto_resolve_mod_dependencies)

    def on_dep_toggled(row, _pspec):
        preferences.auto_resolve_mod_dependencies = row.get_active()

    dep_row.connect("notify::active", on_dep_toggled)
    mods_group.add(dep_row)

    page.add(mods_group)

    # ---------- Remote management ----------
    remote_page = Adw.PreferencesPage(title=_("Remote"), icon_name="network-server-symbolic")
    remote_group = Adw.PreferencesGroup(
        title=_("Remote management"),
        description=_(
            "Serve a management web UI over HTTP so servers can be controlled "
            "from another device (LAN, Tailscale, or behind a reverse proxy)."
        ),
    )

    remote_switch = Adw.SwitchRow(title=_("Remote management"))
    remote_switch.set_active(preferences.remote_management_enabled)
    remote_switch.set_subtitle(_("Disabled"))
    try:
        remote_switch.set_subtitle_lines(2)
    except Exception:
        pass
    remote_group.add(remote_switch)

    daemon_config = load_daemon_config()

    host_row = Adw.EntryRow(title=_("Host"), show_apply_button=True)
    host_row.set_text(daemon_config.get("host", "127.0.0.1"))
    remote_group.add(host_row)

    port_row = Adw.EntryRow(title=_("Port"), show_apply_button=True)
    port_row.set_text(daemon_config.get("port", "25570"))
    remote_group.add(port_row)

    token_row = Adw.PasswordEntryRow(title=_("Access token"))
    token_row.set_text(daemon_config.get("token", ""))
    regenerate_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
    regenerate_button.add_css_class("flat")
    regenerate_button.set_size_request(28, 28)
    regenerate_button.set_valign(Gtk.Align.CENTER)
    regenerate_button.set_tooltip_text(_("Regenerate token"))
    regenerate_button.connect("clicked", lambda _b: _confirm_regenerate())
    token_row.add_suffix(regenerate_button)
    copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
    copy_button.add_css_class("flat")
    copy_button.set_size_request(28, 28)
    copy_button.set_valign(Gtk.Align.CENTER)
    copy_button.set_tooltip_text(_("Copy token"))
    copy_button.connect("clicked", lambda _b: _copy_token())
    token_row.add_suffix(copy_button)
    remote_group.add(token_row)

    def show_alert(title: str, body: str):
        dialog = Adw.AlertDialog.new(title, body)
        dialog.add_response("ok", _("OK"))
        dialog.present(parent)

    def _copy_token():
        text = token_row.get_text()
        if text:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(GObject.Value(GObject.TYPE_STRING, text))
            show_pref_toast(_("Token copied to clipboard"))

    def _confirm_regenerate():
        dialog = Adw.AlertDialog.new(
            _("Regenerate access token?"),
            _("A new random token will be generated. The current token will no longer work."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("regenerate", _("Regenerate"))
        dialog.set_response_appearance("regenerate", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, resp: _do_regenerate() if resp == "regenerate" else None,
        )
        dialog.present(parent)

    def _do_regenerate():
        token_row.set_text(secrets.token_urlsafe(24))
        error = restart_daemon()
        if error:
            show_alert(_("Remote Management Error"), error)
            return
        if remote_switch.get_active():
            remote_switch.set_subtitle(remote_status_text())
        show_pref_toast(_("New token generated"))

    def current_remote_error() -> str | None:
        """Validate the current fields. Returns an error message or None."""
        host = host_row.get_text().strip()
        port_text = port_row.get_text().strip()
        token = token_row.get_text().strip()
        if not host:
            return _("Host cannot be empty.")
        try:
            port = int(port_text)
        except ValueError:
            return _("Port must be a number between 1024 and 65535.")
        if not 1024 <= port <= 65535:
            return _("Port must be a number between 1024 and 65535.")
        if not token:
            return _("An access token is required.")
        return None

    def restart_daemon() -> str | None:
        """Save current fields and (re)start the management server. Returns an error or None."""
        error = current_remote_error()
        if error:
            return error
        host = host_row.get_text().strip()
        port = int(port_row.get_text().strip())
        token = token_row.get_text().strip()
        save_daemon_config(host, port, token)
        if application is None:
            return None
        application._stop_remote_management()
        ok, start_error = application._start_remote_management()
        return None if ok else (start_error or _("Could not start the management server."))

    def remote_status_text() -> str:
        host = host_row.get_text().strip() or "127.0.0.1"
        port = port_row.get_text().strip() or "25570"
        return _("Running at http://{}:{}").format(host, port)

    def on_remote_toggled(row, _pspec):
        if row.get_active():
            error = restart_daemon()
            if error:
                GLib.idle_add(row.set_active, False)
                show_alert(_("Remote Management Error"), error)
                return
            preferences.remote_management_enabled = True
            row.set_subtitle(remote_status_text())
        else:
            preferences.remote_management_enabled = False
            if application is not None:
                application._stop_remote_management()
            row.set_subtitle(_("Disabled"))

    remote_switch.connect("notify::active", on_remote_toggled)

    def on_remote_field_applied(_row):
        """Field changed: persist and restart if the server is running or being enabled."""
        if not remote_switch.get_active():
            return
        error = restart_daemon()
        if error:
            show_alert(_("Remote Management Error"), error)
        else:
            remote_switch.set_subtitle(remote_status_text())

    host_row.connect("apply", on_remote_field_applied)
    port_row.connect("apply", on_remote_field_applied)
    token_row.connect("apply", on_remote_field_applied)

    if preferences.remote_management_enabled and application is not None and application._daemon_host:
        remote_switch.set_subtitle(remote_status_text())

    remote_page.add(remote_group)

    win.add(page)
    win.add(remote_page)

    win.present(parent)
