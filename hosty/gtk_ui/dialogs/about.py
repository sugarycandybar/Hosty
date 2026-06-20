"""
AboutDialog - Hosty about dialog.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from hosty.shared.utils.constants import APP_ID, APP_NAME, APP_VERSION, APP_WEBSITE


def show_about_dialog(parent):
    """Show the Hosty about dialog."""
    about = Adw.AboutDialog()
    about.set_application_name(APP_NAME)
    about.set_application_icon(APP_ID)
    about.set_version(APP_VERSION)
    about.set_developer_name("Sugarycandybar")
    about.set_license_type(Gtk.License.GPL_3_0)
    about.set_comments(
        _("A modern application for creating, running, and managing Fabric Minecraft servers with ease.")
    )
    about.set_website(APP_WEBSITE)
    about.set_issue_url(APP_WEBSITE + "/issues")
    about.add_acknowledgement_section(
        _("Acknowledgements"),
        ["Fabric https://fabricmc.net", "Modrinth https://modrinth.com", "Playit https://playit.gg"],
    )
    about.add_other_app("io.github.sugarycandybar.Crucible", "Crucible", _("View specs and stress test hardware"))
    about.add_other_app("io.github.sugarycandybar.Carabiner", "Carabiner", _("Create and manage network tunnels"))
    about.present(parent)
