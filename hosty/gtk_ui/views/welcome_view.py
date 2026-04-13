"""
WelcomeView - Empty state shown when no server is selected.
Includes its own Adw.HeaderBar with proper window controls.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw


class WelcomeView(Gtk.Box):
    """Welcome/empty state view shown when no server is selected.
    
    Uses Adw.ToolbarView internally so it has a proper HeaderBar with window
    controls visible.
    """
    
    def __init__(self):
        super().__init__()
        
        self._toolbar_view = Adw.ToolbarView()
        self._toolbar_view.set_hexpand(True)
        self._toolbar_view.set_vexpand(True)
        self.append(self._toolbar_view)
        
        # Header bar with window controls
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Hosty"))
        self._toolbar_view.add_top_bar(header)
        
        status = Adw.StatusPage()
        status.set_icon_name("applications-games-symbolic")
        status.set_title("Welcome to Hosty")
        status.set_description(
            "Create and manage your Fabric Minecraft servers\n"
            "with an easy-to-use interface."
        )
        status.set_vexpand(True)
        status.set_hexpand(True)
        
        # Create server button — use standard Adwaita suggested-action
        btn = Gtk.Button(label="Create Server")
        btn.set_halign(Gtk.Align.CENTER)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_action_name("app.new-server")
        
        status.set_child(btn)
        self._toolbar_view.set_content(status)
