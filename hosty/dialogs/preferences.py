"""
Application preferences window (minimal — extend as settings grow).
"""
from __future__ import annotations

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from hosty.utils.constants import (
    APP_VERSION,
    DATA_DIR,
    MIN_RAM_MB,
    MAX_RAM_MB,
)
from hosty.backend.preferences_manager import PreferencesManager


def show_preferences_window(parent: Gtk.Window, preferences: PreferencesManager):
    win = Adw.PreferencesWindow()
    win.set_title("Preferences")
    win.set_default_size(480, 360)
    win.set_modal(True)
    win.set_transient_for(parent)

    page = Adw.PreferencesPage(title="General")
    group = Adw.PreferencesGroup(
        title="Application",
        description="Hosty stores server data under your user data directory.",
    )
    ver = Adw.ActionRow(title="Version", subtitle=APP_VERSION)
    ver.set_activatable(False)
    group.add(ver)
    data_row = Adw.ActionRow(title="Data folder", subtitle=str(DATA_DIR))
    data_row.set_activatable(False)
    group.add(data_row)

    autobackup_row = Adw.SwitchRow(
        title="Auto backup on stop",
        subtitle="Create a world backup whenever a server stops",
    )
    autobackup_row.set_active(preferences.auto_backup_on_stop)

    def on_autobackup_toggled(row, _pspec):
        preferences.auto_backup_on_stop = row.get_active()

    autobackup_row.connect("notify::active", on_autobackup_toggled)
    group.add(autobackup_row)

    dep_row = Adw.SwitchRow(
        title="Auto resolve mod dependencies",
        subtitle="Install required Modrinth dependencies automatically",
    )
    dep_row.set_active(preferences.auto_resolve_mod_dependencies)

    def on_dep_toggled(row, _pspec):
        preferences.auto_resolve_mod_dependencies = row.get_active()

    dep_row.connect("notify::active", on_dep_toggled)
    group.add(dep_row)

    ram_adj = Gtk.Adjustment(
        value=preferences.default_ram_mb,
        lower=MIN_RAM_MB,
        upper=MAX_RAM_MB,
        step_increment=256,
        page_increment=1024,
    )
    ram_row = Adw.SpinRow(
        title="Default RAM (MB)",
        subtitle="Used when creating new servers",
        adjustment=ram_adj,
    )

    def on_ram_changed(row):
        preferences.default_ram_mb = int(row.get_value())

    ram_row.connect("changed", on_ram_changed)
    group.add(ram_row)

    page.add(group)
    win.add(page)

    win.present()
