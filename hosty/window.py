"""
HostyWindow - Main application window with NavigationSplitView.
"""
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from hosty.backend.playit_config import load_playit_config
from hosty.backend.server_manager import ServerManager
from hosty.ui.tray_manager import TrayManager
from hosty.views.sidebar import Sidebar
from hosty.views.server_detail import ServerDetailView
from hosty.views.welcome_view import WelcomeView


class HostyWindow(Adw.ApplicationWindow):
    """Main Hosty application window."""
    
    def __init__(self, server_manager: ServerManager, **kwargs):
        super().__init__(**kwargs)
        self._server_manager = server_manager
        self._current_server_id = None
        self._force_close = False
        self._tray = TrayManager(self._on_tray_restore, self._on_tray_quit)
        self._inhibit_cookie = 0
        self._status_poll_id = None
        self._last_running_server_id = self._server_manager.get_running_server_id()
        self._playit_starting_server_id = None
        
        self.set_title("Hosty")
        self.set_default_size(1100, 700)
        self.set_size_request(400, 400)
        self.add_css_class("hosty-window")
        
        # Toast overlay wraps everything
        self._toast_overlay = Adw.ToastOverlay()
        
        # NavigationSplitView
        self._split_view = Adw.NavigationSplitView()
        
        # ===== Sidebar =====
        self._sidebar = Sidebar(server_manager)
        self._sidebar.connect('server-selected', self._on_server_selected)
        
        sidebar_page = Adw.NavigationPage(
            title="Servers",
            child=self._sidebar,
        )
        self._split_view.set_sidebar(sidebar_page)
        
        # ===== Content =====
        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_transition_duration(200)
        
        # Welcome view
        self._welcome_view = WelcomeView()
        self._content_stack.add_named(self._welcome_view, "welcome")
        
        # Server detail view
        self._detail_view = ServerDetailView(server_manager)
        self._content_stack.add_named(self._detail_view, "detail")
        
        # Content wrapper with navigation page
        content_page = Adw.NavigationPage(
            title="Server",
            child=self._content_stack,
        )
        self._split_view.set_content(content_page)
        
        # Responsive breakpoint
        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 600sp")
        )
        breakpoint.add_setter(self._split_view, "collapsed", True)
        self.add_breakpoint(breakpoint)
        
        self._toast_overlay.set_child(self._split_view)
        self.set_content(self._toast_overlay)
        
        # Show welcome or auto-select first server
        if server_manager.servers:
            first_id = server_manager.servers[0].id
            GLib.idle_add(lambda: self._sidebar.select_server(first_id))
        else:
            self._content_stack.set_visible_child_name("welcome")
        
        # Connect server add to switch content
        server_manager.connect('server-added', self._on_server_added)
        server_manager.connect('server-removed', self._on_server_removed)
        self.connect("close-request", self._on_close_request)
        self.connect("notify::visible", self._on_visible_changed)

        self._status_poll_id = GLib.timeout_add(1000, self._poll_runtime_state)
    
    def _on_server_selected(self, sidebar, server_id):
        """Handle server selection from sidebar."""
        if not server_id:
            self._content_stack.set_visible_child_name("welcome")
            return
        
        self._current_server_id = server_id
        server_info = self._server_manager.get_server(server_id)
        
        if server_info:
            self._detail_view.load_server(server_info)
            self._content_stack.set_visible_child_name("detail")
            
            # Show content in collapsed mode
            if self._split_view.get_collapsed():
                self._split_view.set_show_content(True)
    
    def _on_server_added(self, manager, server_id):
        """Handle new server added - switch to it."""
        # The sidebar handles adding the row and selecting it
        pass

    def _on_server_removed(self, manager, server_id):
        """Return to welcome when no servers remain."""
        if not self._server_manager.servers:
            self._current_server_id = None
            self._content_stack.set_visible_child_name("welcome")

    def _on_close_request(self, window):
        """Keep app alive in background using a tray icon when enabled."""
        if self._force_close:
            return False

        prefs = self._server_manager.preferences
        if prefs.run_in_background_on_close:
            if self._tray.show():
                self.hide()
                return True

            # Fallback when tray backend is unavailable.
            self.hide()
            self.show_toast("Tray backend not available, Hosty is running in background")
            return True
        return False

    def _on_visible_changed(self, *_args):
        if self.get_visible():
            self._tray.hide()

    def _on_tray_restore(self):
        self._tray.hide()
        self.present()

    def _on_tray_quit(self):
        self._force_close = True
        self._tray.hide()
        self._set_sleep_inhibit(False)
        app = self.get_application()
        if app:
            app.quit()

    def restore_from_background(self):
        """Bring window back when app is re-activated from launcher."""
        self._tray.hide()

    def shutdown_background(self):
        """Ensure tray resources are released on app shutdown."""
        if self._status_poll_id:
            GLib.source_remove(self._status_poll_id)
            self._status_poll_id = None

        self._set_sleep_inhibit(False)
        self._tray.hide()

    def _poll_runtime_state(self):
        running_id = self._server_manager.get_running_server_id()
        prefs = self._server_manager.preferences

        if running_id != self._last_running_server_id:
            previous_id = self._last_running_server_id
            self._last_running_server_id = running_id

            if running_id:
                running_info = self._server_manager.get_server(running_id)
                running_name = running_info.name if running_info else "Server"
                if prefs.prevent_sleep_while_running:
                    self._set_sleep_inhibit(True, f"{running_name} is running")

                self._apply_playit_runtime(previous_id, running_id)
            else:
                self._set_sleep_inhibit(False)
                self._apply_playit_runtime(previous_id, None)

                if previous_id:
                    if prefs.auto_backup_on_stop:
                        self._start_auto_backup(previous_id)
        else:
            if running_id and prefs.prevent_sleep_while_running and self._inhibit_cookie == 0:
                self._set_sleep_inhibit(True, "Hosty server is running")
            elif (not running_id) or (not prefs.prevent_sleep_while_running):
                self._set_sleep_inhibit(False)

            # Keep playit in sync even when server runtime does not change.
            self._apply_playit_runtime(None, running_id)

        return True

    def _load_playit_config(self, server_id: str) -> dict:
        info = self._server_manager.get_server(server_id)
        if not info:
            return {}
        return load_playit_config(info.server_dir)

    def _apply_playit_runtime(self, previous_id: str | None, running_id: str | None):
        playit = self._server_manager.playit_manager

        # Stop the current tunnel if the associated server stopped.
        if previous_id and previous_id != running_id and playit.is_running_for(previous_id):
            playit.stop()

        if not running_id:
            return

        cfg = self._load_playit_config(running_id)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("auto_start", True):
            return

        if playit.is_running_for(running_id):
            return

        if self._playit_starting_server_id == running_id:
            return

        info = self._server_manager.get_server(running_id)
        if not info:
            return

        self._playit_starting_server_id = running_id

        def worker():
            playit.start(
                running_id,
                str(info.server_dir),
                secret=str(cfg.get("secret", "")).strip(),
                auto_install=bool(cfg.get("auto_install", True)),
            )

            def clear_starting_flag():
                if self._playit_starting_server_id == running_id:
                    self._playit_starting_server_id = None

            GLib.idle_add(clear_starting_flag)

        threading.Thread(target=worker, daemon=True).start()

    def _set_sleep_inhibit(self, enable: bool, reason: str = ""):
        app = self.get_application()
        if not app:
            return

        if enable:
            if self._inhibit_cookie != 0:
                return
            try:
                self._inhibit_cookie = app.inhibit(
                    self,
                    Gtk.ApplicationInhibitFlags.IDLE,
                    reason or "Hosty server is running",
                )
            except Exception:
                self._inhibit_cookie = 0
        else:
            if self._inhibit_cookie == 0:
                return
            try:
                app.uninhibit(self._inhibit_cookie)
            except Exception:
                pass
            self._inhibit_cookie = 0

    def _start_auto_backup(self, server_id: str):
        def worker():
            ok, msg = self._server_manager.create_world_backup(server_id, auto=True)

            def ui_done():
                if ok:
                    self.show_toast(f"Auto backup created: {msg}")
                else:
                    self.show_toast(f"Auto backup skipped: {msg}")

            GLib.idle_add(ui_done)

        threading.Thread(target=worker, daemon=True).start()
    
    def show_toast(self, message: str):
        """Show a toast notification."""
        toast = Adw.Toast(title=message)
        toast.set_timeout(3)
        self._toast_overlay.add_toast(toast)
    
    @property
    def sidebar(self):
        return self._sidebar
    
    @property
    def detail_view(self):
        return self._detail_view
    
    @property
    def current_server_id(self):
        return self._current_server_id
