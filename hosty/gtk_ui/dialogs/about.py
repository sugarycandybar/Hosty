"""
AboutDialog - Hosty about dialog.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from hosty.shared.utils.constants import APP_ID, APP_NAME, APP_VERSION, APP_WEBSITE


def show_about_dialog(parent):
    """Show the Hosty about dialog."""
    about = Adw.AboutDialog()
    about.set_application_name(APP_NAME)
    about.set_application_icon(APP_ID)
    about.set_version(APP_VERSION)
    about.set_developer_name("Hosty Project")
    about.set_license_type(Gtk.License.GPL_3_0)
    about.set_comments(
        "A modern application for creating, running, and managing\n"
        "Fabric Minecraft servers with ease."
    )
    about.set_website(APP_WEBSITE)
    about.set_developers(["Hosty Contributors"])
    about.present(parent)
