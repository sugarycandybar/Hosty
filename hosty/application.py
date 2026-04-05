"""
HostyApplication - Main Adw.Application subclass.
Handles app lifecycle, actions, CSS loading, and dialog management.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from pathlib import Path

from hosty.utils.constants import APP_ID, APP_NAME, APP_VERSION
from hosty.backend.server_manager import ServerManager
from hosty.window import HostyWindow
from hosty.core.events import set_main_thread_dispatcher


class HostyApplication(Adw.Application):
    """Main Hosty application."""
    
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._server_manager = None
        self._window = None
    
    def do_startup(self):
        """Application startup - load CSS and setup actions."""
        Adw.Application.do_startup(self)

        set_main_thread_dispatcher(
            lambda callback, *args, **kwargs: GLib.idle_add(callback, *args, **kwargs)
        )
        
        # Load custom CSS
        self._load_css()
        
        # Initialize server manager
        self._server_manager = ServerManager()
        
        # Setup actions
        self._setup_actions()
    
    def do_activate(self):
        """Application activate - show the window."""
        if not self._window:
            self._window = HostyWindow(
                server_manager=self._server_manager,
                application=self,
            )
        
        self._window.present()
    
    def do_shutdown(self):
        """Application shutdown - stop all servers."""
        if self._server_manager:
            self._server_manager.stop_all()
        set_main_thread_dispatcher(None)
        Adw.Application.do_shutdown(self)
    
    def _load_css(self):
        """Load custom CSS stylesheet."""
        css_provider = Gtk.CssProvider()
        css_path = Path(__file__).parent / "style.css"
        
        if css_path.exists():
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
    
    def _setup_actions(self):
        """Register application actions."""
        # New server
        action_new = Gio.SimpleAction.new("new-server", None)
        action_new.connect("activate", self._on_new_server)
        self.add_action(action_new)
        
        # About
        action_about = Gio.SimpleAction.new("about", None)
        action_about.connect("activate", self._on_about)
        self.add_action(action_about)
        
        action_prefs = Gio.SimpleAction.new("preferences", None)
        action_prefs.connect("activate", self._on_preferences)
        self.add_action(action_prefs)
        
        # Rename server (parameterized)
        action_rename = Gio.SimpleAction.new("rename-server", GLib.VariantType.new("s"))
        action_rename.connect("activate", self._on_rename_server)
        self.add_action(action_rename)
        
        # Change icon (parameterized)
        action_icon = Gio.SimpleAction.new("change-icon", GLib.VariantType.new("s"))
        action_icon.connect("activate", self._on_change_icon)
        self.add_action(action_icon)
        
        # Delete server (parameterized)
        action_delete = Gio.SimpleAction.new("delete-server", GLib.VariantType.new("s"))
        action_delete.connect("activate", self._on_delete_server)
        self.add_action(action_delete)
        
        # Keyboard shortcuts
        self.set_accels_for_action("app.new-server", ["<Primary>n"])
        self.set_accels_for_action("app.about", ["<Primary>question"])
        self.set_accels_for_action("app.preferences", ["<Primary>comma"])
    
    def _on_new_server(self, action, param):
        """Show create server dialog."""
        from hosty.dialogs.create_server import CreateServerDialog
        
        dialog = CreateServerDialog(self._server_manager)
        dialog.connect('server-created', self._on_server_created)
        dialog.present(self._window)
    
    def _on_server_created(self, dialog, server_id):
        """Handle newly created server."""
        if self._window:
            self._window.show_toast("Server created successfully!")
    
    def _on_about(self, action, param):
        """Show about dialog."""
        from hosty.dialogs.about import show_about_dialog
        show_about_dialog(self._window)
    
    def _on_preferences(self, action, param):
        """Show application preferences."""
        from hosty.dialogs.preferences import show_preferences_window
        if self._window:
            show_preferences_window(self._window)
    
    def _on_rename_server(self, action, param):
        """Show rename dialog for a server."""
        server_id = param.get_string()
        server_info = self._server_manager.get_server(server_id)
        if not server_info:
            return
        
        # Use Adw.AlertDialog for rename
        dialog = Adw.AlertDialog()
        dialog.set_heading("Rename Server")
        dialog.set_body("Enter a new name for the server:")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")
        
        # Add entry as extra child
        entry = Gtk.Entry()
        entry.set_text(server_info.name)
        entry.set_margin_start(24)
        entry.set_margin_end(24)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        
        def on_response(d, response):
            if response == "rename":
                new_name = entry.get_text().strip()
                if new_name:
                    self._server_manager.rename_server(server_id, new_name)
                    # Update detail view if this is the current server
                    if self._window and self._window.current_server_id == server_id:
                        info = self._server_manager.get_server(server_id)
                        if info:
                            self._window.detail_view.load_server(info)
                    self._window.show_toast(f"Server renamed to \"{new_name}\"")
        
        dialog.connect("response", on_response)
        dialog.present(self._window)
    
    def _on_change_icon(self, action, param):
        """Show icon picker dialog for a server."""
        server_id = param.get_string()
        server_info = self._server_manager.get_server(server_id)
        if not server_info:
            return
        
        from hosty.dialogs.icon_picker import IconPickerDialog
        dialog = IconPickerDialog(server_id, str(server_info.server_dir))
        
        def on_icon_selected(d, icon_path):
            self._server_manager.set_server_icon(server_id, icon_path)
            self._window.show_toast("Server icon updated")
        
        dialog.connect('icon-selected', on_icon_selected)
        dialog.present(self._window)
    
    def _on_delete_server(self, action, param):
        """Show delete confirmation for a server."""
        server_id = param.get_string()
        server_info = self._server_manager.get_server(server_id)
        if not server_info:
            return
        
        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete Server?")
        dialog.set_body(
            f"Are you sure you want to delete \"{server_info.name}\"?\n\n"
            f"All server files will be permanently deleted."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        
        def on_response(d, response):
            if response == "delete":
                self._server_manager.delete_server(server_id, delete_files=True)
                self._window.show_toast(f"Server \"{server_info.name}\" deleted")
        
        dialog.connect("response", on_response)
        dialog.present(self._window)
