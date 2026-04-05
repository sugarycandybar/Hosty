"""
HostyWindow - Main application window with NavigationSplitView.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from hosty.backend.server_manager import ServerManager
from hosty.views.sidebar import Sidebar
from hosty.views.server_detail import ServerDetailView
from hosty.views.welcome_view import WelcomeView


class HostyWindow(Adw.ApplicationWindow):
    """Main Hosty application window."""
    
    def __init__(self, server_manager: ServerManager, **kwargs):
        super().__init__(**kwargs)
        self._server_manager = server_manager
        self._current_server_id = None
        
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
