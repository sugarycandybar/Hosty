"""
HostyApplication - Main Adw.Application subclass.
Handles app lifecycle, actions, CSS loading, and dialog management.
"""

import shutil
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from hosty.gtk_ui.window import HostyWindow
from hosty.i18n import setup_gettext
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.core.events import set_main_thread_dispatcher
from hosty.shared.utils.constants import APP_ID


class HostyApplication(Adw.Application):
    """Main Hosty application."""

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._server_manager = None
        self._window = None
        self._activate_in_background = False
        self._is_held_for_background = False
        self._tray_manager = None
        self._shortcuts_dialog = None

    def do_command_line(self, command_line):
        """Handle command line arguments."""
        args = command_line.get_arguments()
        self._activate_in_background = "--background" in args
        self.activate()
        return 0

    def do_startup(self):
        """Application startup - load CSS and setup actions."""
        Adw.Application.do_startup(self)

        set_main_thread_dispatcher(lambda callback, *args, **kwargs: GLib.idle_add(callback, *args, **kwargs))

        setup_gettext()

        # Load custom CSS
        self._load_css()
        self._register_packaged_icons()

        # Initialize server manager
        self._server_manager = ServerManager()

        # Setup actions
        self._setup_actions()

        if sys.platform == "win32":
            # Synchronize Windows registry startup run key with preferences
            try:
                import winreg

                key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                app_name = "Hosty"
                autostart = self._server_manager.preferences.open_on_startup
                if autostart:
                    if getattr(sys, "frozen", False):
                        cmd = f'"{sys.executable}" --background'
                    else:
                        cmd = f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}" --background'
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                    winreg.CloseKey(key)
                else:
                    try:
                        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                        try:
                            winreg.DeleteValue(key, app_name)
                        except FileNotFoundError:
                            pass
                        winreg.CloseKey(key)
                    except Exception:
                        pass
            except Exception:
                pass

            # Start the single-instance IPC listener so a second launch shows this window
            from hosty.shared.utils.windows_instance import start_show_listener

            start_show_listener(self._on_instance_show_requested)

            # Initialize and start the Windows system tray manager
            from hosty.shared.utils.tray_windows import WindowsTrayManager

            self._tray_manager = WindowsTrayManager(self)
            try:
                self._tray_manager.start()
            except Exception:
                pass

    def do_activate(self):
        """Application activate - show the window."""
        # Ensure CSS and icons are loaded in case display was initialized late (e.g. Broadway backend)
        self._load_css()
        self._register_packaged_icons()

        if not hasattr(self, "_autostarted_once"):
            self._autostarted_once = True
            for autostart_server in self._server_manager.get_autostart_servers():
                proc = self._server_manager.get_process(autostart_server.id)
                if proc and not proc.is_running:
                    proc.start()

        if self._activate_in_background:
            self._activate_in_background = False
            if not self._is_held_for_background:
                self.hold()
                self._is_held_for_background = True

            # Start background services but don't show the window yet
            if not self._window:
                self._window = HostyWindow(
                    server_manager=self._server_manager,
                    application=self,
                )
            self._window.restore_from_background()
            return

        if not self._window:
            self._window = HostyWindow(
                server_manager=self._server_manager,
                application=self,
            )

        self._window.restore_from_background()
        self._window.present()

    def do_shutdown(self):
        """Application shutdown - stop all servers."""
        if sys.platform == "win32":
            from hosty.shared.utils.windows_instance import cleanup as cleanup_instance

            cleanup_instance()

        if sys.platform == "win32" and hasattr(self, "_tray_manager") and self._tray_manager:
            self._tray_manager.stop()
            self._tray_manager = None

        if self._window:
            self._window.shutdown_background()
        if self._server_manager:
            self._server_manager.stop_all()
        set_main_thread_dispatcher(None)
        Adw.Application.do_shutdown(self)

    def _load_css(self):
        """Load custom CSS stylesheet."""
        display = Gdk.Display.get_default()
        if not display:
            return
        css_provider = Gtk.CssProvider()
        css_path = Path(__file__).parent / "style.css"

        if css_path.exists():
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _register_packaged_icons(self):
        """Ensure app icons are discoverable in development and frozen runs."""
        display = Gdk.Display.get_default()
        if not display:
            return

        icon_theme = Gtk.IconTheme.get_for_display(display)

        candidates = []
        if getattr(sys, "frozen", False):
            bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            candidates.append(bundle_dir / "share" / "icons")
            candidates.append(bundle_dir / "icons")

        candidates.append(Path(__file__).resolve().parents[2] / "packaging" / "linux")

        for icon_dir in candidates:
            if icon_dir.exists():
                icon_theme.add_search_path(str(icon_dir))

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

        action_shortcuts = Gio.SimpleAction.new("shortcuts", None)
        action_shortcuts.connect("activate", self._on_shortcuts)
        self.add_action(action_shortcuts)

        # Rename server (parameterized)
        action_rename = Gio.SimpleAction.new("rename-server", GLib.VariantType.new("s"))
        action_rename.connect("activate", self._on_rename_server)
        self.add_action(action_rename)

        # Change icon (parameterized)
        action_icon = Gio.SimpleAction.new("change-icon", GLib.VariantType.new("s"))
        action_icon.connect("activate", self._on_change_icon)
        self.add_action(action_icon)

        # Quit
        action_quit = Gio.SimpleAction.new("quit", None)
        action_quit.connect("activate", self._on_quit)
        self.add_action(action_quit)

        # Delete server (parameterized)
        action_delete = Gio.SimpleAction.new("delete-server", GLib.VariantType.new("s"))
        action_delete.connect("activate", self._on_delete_server)
        self.add_action(action_delete)

        # Keyboard shortcuts
        self.set_accels_for_action("app.new-server", ["<Primary>n"])
        self.set_accels_for_action("app.preferences", ["<Primary>comma"])
        self.set_accels_for_action("app.shortcuts", ["<Primary>question"])
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("win.close-window", ["<Primary>w"])

    def _on_new_server(self, action, param):
        """Show create server dialog."""
        from hosty.gtk_ui.dialogs.create_server import CreateServerDialog

        dialog = CreateServerDialog(self._server_manager)
        dialog.connect("server-created", self._on_server_created)
        dialog.present(self._window)

    def _on_server_created(self, dialog, server_id):
        """Handle newly created server."""
        if not self._window:
            return

        self._window.sidebar.select_server(server_id)
        info = self._server_manager.get_server(server_id)
        if info:
            self._window.detail_view.load_server(info)
        self._window.show_toast(_("Server created"))

    def _on_about(self, action, param):
        """Show about dialog."""
        from hosty.gtk_ui.dialogs.about import show_about_dialog

        show_about_dialog(self._window)

    def _on_quit(self, action, param):
        """Quit the application."""
        if self._window:
            self._window._quit_requested = True
            self._window.close()
        else:
            self.quit()

    def _on_preferences(self, action, param):
        """Show application preferences."""
        from hosty.gtk_ui.dialogs.preferences import show_preferences_window

        if self._window:
            show_preferences_window(self._window, self._server_manager.preferences, self._server_manager)

    def _on_shortcuts(self, action, param):
        """Show keyboard shortcuts."""
        if not self._window:
            return

        if self._shortcuts_dialog is None:
            from hosty.gtk_ui.dialogs.shortcuts import create_shortcuts_dialog

            self._shortcuts_dialog = create_shortcuts_dialog()

        self._shortcuts_dialog.present(self._window)

    def _on_rename_server(self, action, param):
        """Show rename dialog for a server."""
        server_id = param.get_string()
        server_info = self._server_manager.get_server(server_id)
        if not server_info:
            return

        # Use Adw.AlertDialog for rename
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Rename Server"))
        dialog.set_body(_("Enter a new name for the server:"))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
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
                    self._window.show_toast(_('Server renamed to "{}"').format(new_name))

        dialog.connect("response", on_response)
        dialog.present(self._window)

    def _on_change_icon(self, action, param):
        """Show icon picker dialog for a server."""
        server_id = param.get_string()
        server_info = self._server_manager.get_server(server_id)
        if not server_info:
            return

        from hosty.gtk_ui.dialogs.icon_picker import IconPickerDialog

        dialog = IconPickerDialog(server_id, str(server_info.server_dir))

        def on_icon_selected(d, icon_path):
            self._server_manager.set_server_icon(server_id, icon_path)
            self._window.show_toast(_("Server icon updated"))

        dialog.connect("icon-selected", on_icon_selected)
        dialog.present(self._window)

    def _on_delete_server(self, action, param):
        """Show delete confirmation for a server."""
        server_id = param.get_string()
        server_info = self._server_manager.get_server(server_id)
        if not server_info:
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete Server?"))
        dialog.set_body(
            _('Are you sure you want to delete "{}"?\n\n{}').format(
                server_info.name, _("All server files will be permanently deleted.")
            )
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(d, response):
            if response == "delete":
                server_snapshot = server_info.to_dict()
                server_dir = server_info.server_dir
                state = {"undone": False}

                self._server_manager.delete_server(server_id, delete_files=False)

                def undo_delete():
                    if state["undone"]:
                        return
                    state["undone"] = True
                    if self._server_manager.restore_server(server_snapshot):
                        self._window.sidebar.select_server(server_id)
                        info = self._server_manager.get_server(server_id)
                        if info:
                            self._window.detail_view.load_server(info)
                        self._window.show_toast(_('Server "{}" restored').format(server_info.name))
                    else:
                        self._window.show_toast(_("Could not restore deleted server"))

                def finalize_delete():
                    if state["undone"]:
                        return False
                    try:
                        shutil.rmtree(server_dir, ignore_errors=True)
                    except Exception:
                        pass
                    return False

                self._window.show_toast(
                    _('Server "{}" deleted').format(server_info.name),
                    button_label=_("Undo"),
                    on_button=undo_delete,
                    timeout=6,
                )
                GLib.timeout_add_seconds(6, finalize_delete)

        dialog.connect("response", on_response)
        dialog.present(self._window)

    def _on_instance_show_requested(self):
        """Bring the window to front when signalled by a second instance."""
        if not self._window:
            return
        if self._is_held_for_background:
            self.release()
            self._is_held_for_background = False
        self._window.set_visible(True)
        self._window.present()
