"""
Keyboard shortcuts dialog for Hosty.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gtk


def create_shortcuts_dialog() -> Adw.ShortcutsDialog:
    dialog = Adw.ShortcutsDialog()

    key_controller = Gtk.EventControllerKey()
    key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def on_key_pressed(controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            dialog.close()
            return True
        return False

    key_controller.connect("key-pressed", on_key_pressed)
    dialog.add_controller(key_controller)

    general = Adw.ShortcutsSection(title=_("General"))
    general.add(Adw.ShortcutsItem.new(_("Create New Server"), "<Primary>n"))
    general.add(Adw.ShortcutsItem.new(_("Open Preferences"), "<Primary>comma"))
    general.add(Adw.ShortcutsItem.new(_("Open Menu"), "F10"))
    general.add(Adw.ShortcutsItem.new(_("Show Keyboard Shortcuts"), "<Primary>question"))
    general.add(Adw.ShortcutsItem.new(_("Quit App"), "<Primary>q"))
    general.add(Adw.ShortcutsItem.new(_("Close Window"), "<Primary>w"))

    dialog.add(general)

    return dialog
