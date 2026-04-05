"""
Application preferences window (minimal — extend as settings grow).
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from hosty.utils.constants import APP_NAME, APP_VERSION, DATA_DIR


def show_preferences_window(parent: Gtk.Window):
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
    page.add(group)
    win.add(page)

    win.present()
