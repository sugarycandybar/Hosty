"""
Application preferences window (minimal — extend as settings grow).
"""
from __future__ import annotations

import subprocess
import sys
import webbrowser

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from hosty.shared.backend.playit_config import load_playit_config, save_playit_config
from hosty.shared.utils.constants import (
    APP_VERSION,
    DATA_DIR,
)
from hosty.shared.backend.preferences_manager import PreferencesManager
from hosty.shared.backend.server_manager import ServerManager


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


def show_preferences_window(parent: Gtk.Window, preferences: PreferencesManager, server_manager: ServerManager | None = None):
    win = Adw.PreferencesWindow()
    win.set_title("Preferences")
    win.set_default_size(700, 560)
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

    page.add(group)

    playit_group = Adw.PreferencesGroup(
        title="Playit.gg",
        description="Reset the current Playit link and open setup in your browser.",
    )
    reset_row = Adw.ActionRow(
        title="Set Up Playit Again",
        subtitle="Clear linked key and open setup page",
    )

    reset_btn = Gtk.Button(label="Open Setup")
    reset_btn.add_css_class("suggested-action")

    def on_reset_playit(_btn):
        if not server_manager:
            return

        manager = server_manager.playit_manager
        try:
            manager.stop()
        except Exception:
            pass

        manager.unlink_account()

        for info in server_manager.servers:
            cfg = load_playit_config(info.server_dir)
            cfg["secret"] = ""
            cfg["enabled"] = False
            cfg["setup_complete"] = False
            save_playit_config(info.server_dir, cfg)

        _open_uri(manager.setup_url)

        if hasattr(parent, "show_toast"):
            parent.show_toast("Playit link reset. Continue setup in browser.")

    reset_btn.connect("clicked", on_reset_playit)
    reset_row.add_suffix(reset_btn)
    playit_group.add(reset_row)
    page.add(playit_group)

    win.add(page)

    win.present()
