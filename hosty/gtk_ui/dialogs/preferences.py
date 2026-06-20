"""
Application preferences window
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from hosty.shared.backend.preferences_manager import PreferencesManager
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.utils.constants import (
    DATA_DIR,
)


def _open_data_folder() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        os.startfile(str(DATA_DIR))
        return

    Gio.AppInfo.launch_default_for_uri(DATA_DIR.as_uri())


def show_preferences_window(
    parent: Gtk.Window, preferences: PreferencesManager, server_manager: ServerManager | None = None
):
    win = Adw.PreferencesDialog()
    # Properties like default_size or modal are handled differently in Adw.Dialog
    # if at all, but we can set them if supported or skip them.

    page = Adw.PreferencesPage(title=_("General"))
    group = Adw.PreferencesGroup(
        title=_("Application"),
    )
    data_row = Adw.ActionRow(title=_("Data folder"), subtitle=str(DATA_DIR))
    data_button = Gtk.Button(valign=Gtk.Align.CENTER)
    data_image = Gtk.Image.new_from_icon_name("folder-open-symbolic")
    data_button.set_child(data_image)
    data_button.connect("clicked", lambda _: _open_data_folder())
    data_row.add_suffix(data_button)
    group.add(data_row)

    bg_row = Adw.SwitchRow(
        title=_("Run in background"),
    )
    bg_row.set_active(preferences.run_in_background_on_close)

    startup_row = Adw.SwitchRow(
        title=_("Open Hosty on startup"),
    )
    startup_row.set_active(preferences.open_on_startup)
    startup_row.set_sensitive(preferences.run_in_background_on_close)

    def on_bg_toggled(row, _pspec):
        active = row.get_active()
        preferences.run_in_background_on_close = active
        startup_row.set_sensitive(active)

        if not active and startup_row.get_active():
            startup_row.set_active(False)

        if active:
            # If turning on background
            from hosty.shared.utils.portal import request_background

            def on_bg_response(success, bg, auto, err):
                if not success or not bg:
                    GLib.idle_add(row.set_active, False)
                    GLib.idle_add(preferences.__setattr__, "run_in_background_on_close", False)

            request_background(False, on_bg_response)

    bg_row.connect("notify::active", on_bg_toggled)

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

    group.add(bg_row)
    group.add(startup_row)

    autobackup_row = Adw.SwitchRow(
        title=_("Auto backup world on stop"),
    )
    autobackup_row.set_active(preferences.auto_backup_on_stop)

    def on_autobackup_toggled(row, _pspec):
        preferences.auto_backup_on_stop = row.get_active()

    autobackup_row.connect("notify::active", on_autobackup_toggled)
    group.add(autobackup_row)

    dep_row = Adw.SwitchRow(
        title=_("Auto resolve mod dependencies"),
    )
    dep_row.set_active(preferences.auto_resolve_mod_dependencies)

    def on_dep_toggled(row, _pspec):
        preferences.auto_resolve_mod_dependencies = row.get_active()

    dep_row.connect("notify::active", on_dep_toggled)
    group.add(dep_row)

    page.add(group)

    win.add(page)

    win.present(parent)
