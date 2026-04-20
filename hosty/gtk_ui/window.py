"""
HostyWindow - Main application window with NavigationSplitView.
"""
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from hosty.shared.backend.playit_config import load_playit_config
from hosty.shared.backend.server_manager import ServerManager
from hosty.gtk_ui.views.sidebar import Sidebar
from hosty.gtk_ui.views.server_detail import ServerDetailView
from hosty.gtk_ui.views.welcome_view import WelcomeView


class HostyWindow(Adw.ApplicationWindow):
    """Main Hosty application window."""
    
    def __init__(self, server_manager: ServerManager, **kwargs):
        super().__init__(**kwargs)
        self._server_manager = server_manager
        self._current_server_id = None
        self._status_poll_id = None
        self._last_running_server_id = self._server_manager.get_running_server_id()
        self._playit_starting_server_id = None
        self._playit_autostart_paused_server_id: str | None = None
        
        self.set_title("Hosty")
        self.set_default_size(1000, 700)
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
        self._sidebar_page = sidebar_page
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
        
        # Show welcome or auto-select first server immediately to avoid welcome flicker.
        if server_manager.servers:
            first_id = server_manager.servers[0].id
            self._sidebar.select_server(first_id)
        else:
            self._content_stack.set_visible_child_name("welcome")
        
        # Connect server add to switch content
        server_manager.connect('server-added', self._on_server_added)
        server_manager.connect('server-removed', self._on_server_removed)

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
        """Return to welcome when current selection is removed or list is empty."""
        if self._current_server_id == server_id or not self._server_manager.servers:
            self._current_server_id = None
            self._content_stack.set_visible_child_name("welcome")

    def restore_from_background(self):
        """Compatibility no-op after removing background mode."""
        return

    def shutdown_background(self):
        """Compatibility no-op after removing background mode."""
        if self._status_poll_id:
            GLib.source_remove(self._status_poll_id)
            self._status_poll_id = None

    def _poll_runtime_state(self):
        self._detail_view.poll_runtime_state()

        running_id = self._server_manager.get_running_server_id()
        prefs = self._server_manager.preferences

        if running_id != self._last_running_server_id:
            previous_id = self._last_running_server_id
            self._last_running_server_id = running_id

            if running_id:
                self._apply_playit_runtime(previous_id, running_id)
            else:
                self._apply_playit_runtime(previous_id, None)

                if previous_id:
                    if prefs.auto_backup_on_stop:
                        self._start_auto_backup(previous_id)
        else:
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

        if running_id != self._playit_autostart_paused_server_id and previous_id == self._playit_autostart_paused_server_id:
            self._playit_autostart_paused_server_id = None

        # Stop the current tunnel if the associated server stopped.
        if previous_id and previous_id != running_id and playit.is_running_for(previous_id):
            playit.stop()

        if not running_id:
            self._playit_autostart_paused_server_id = None
            return

        if running_id == self._playit_autostart_paused_server_id:
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
    
    def show_toast(
        self,
        message: str,
        button_label: str | None = None,
        on_button=None,
        timeout: int = 3,
    ):
        """Show a toast notification."""
        toast = Adw.Toast(title=message)
        toast.set_timeout(max(1, int(timeout)))
        if button_label:
            toast.set_button_label(button_label)
            if on_button:
                toast.connect("button-clicked", lambda *_args: on_button())
        self._toast_overlay.add_toast(toast)

    def pause_playit_auto_start_for_running_server(self, server_id: str):
        self._playit_autostart_paused_server_id = server_id

    def clear_playit_auto_start_pause(self, server_id: str):
        if self._playit_autostart_paused_server_id == server_id:
            self._playit_autostart_paused_server_id = None
    
    @property
    def sidebar(self):
        return self._sidebar
    
    @property
    def detail_view(self):
        return self._detail_view
    
    @property
    def current_server_id(self):
        return self._current_server_id
