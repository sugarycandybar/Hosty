"""
Sidebar - Server list sidebar with status indicators and management.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Adw, GLib, Gdk, GdkPixbuf, GObject, Gio

from hosty.backend.server_manager import ServerManager, ServerInfo
from hosty.backend.server_process import ServerProcess
from hosty.utils.constants import ServerStatus
from hosty.utils.image_utils import load_pixbuf, get_default_server_icon_pixbuf


class ServerRow(Adw.ActionRow):
    """A server entry in the sidebar list."""
    
    def __init__(self, server_info: ServerInfo, server_manager: ServerManager):
        super().__init__()
        self.server_info = server_info
        self._server_manager = server_manager
        self._process = None
        self._status_handler_id = None
        self._players_handler_id = None
        
        self.set_title(server_info.name)
        self.set_subtitle(self._subtitle_text())
        self.set_tooltip_text(server_info.mc_version)
        self.set_activatable(True)
        
        # Server icon
        self._avatar = Adw.Avatar(size=42, text=server_info.name, show_initials=True)
        self._avatar.add_css_class("server-avatar")
        self._update_icon()
        self.add_prefix(self._avatar)
        
        # Status dot
        self._status_dot = Gtk.Box()
        self._status_dot.set_size_request(10, 10)
        self._status_dot.add_css_class("status-dot")
        self._status_dot.add_css_class("stopped")
        self._status_dot.set_valign(Gtk.Align.CENTER)
        self.add_suffix(self._status_dot)
        
        # Connect to process status changes
        process = server_manager.get_process(server_info.id)
        if process:
            self._process = process
            self._status_handler_id = process.connect('status-changed', self._on_status_changed)
            self._players_handler_id = process.connect('players-changed', self._on_players_changed)
            self._update_status(process.status)

    def _subtitle_text(self) -> str:
        if self._process and self._process.is_running:
            return f"{self.server_info.mc_version} · {self._process.player_count}/{self._process.max_players}"
        return self.server_info.mc_version
    
    def _update_icon(self):
        """Update the avatar icon from the server's icon path."""
        if self.server_info.icon_path:
            try:
                texture = Gdk.Texture.new_for_pixbuf(
                    load_pixbuf(self.server_info.icon_path, 42)
                )
                paintable = texture
                self._avatar.set_custom_image(paintable)
            except Exception:
                pass
    
    def _on_status_changed(self, process, status):
        """Handle process status change."""
        self._update_status(status)
        self.set_subtitle(self._subtitle_text())

    def _on_players_changed(self, process, player_count, max_players):
        """Handle player count updates from server output."""
        self.set_subtitle(self._subtitle_text())
    
    def _update_status(self, status: str):
        """Update the status dot."""
        for cls in ["running", "starting", "stopping", "stopped"]:
            self._status_dot.remove_css_class(cls)
        self._status_dot.add_css_class(status)
    
    def update_info(self, server_info: ServerInfo):
        """Update displayed info."""
        self.server_info = server_info
        self.set_title(server_info.name)
        self.set_subtitle(self._subtitle_text())
        self.set_tooltip_text(server_info.mc_version)
        self._update_icon()


class Sidebar(Gtk.Box):
    """Server list sidebar."""
    
    __gsignals__ = {
        'server-selected': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }
    
    def __init__(self, server_manager: ServerManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._server_manager = server_manager
        
        self._toolbar_view = Adw.ToolbarView()
        self._toolbar_view.set_hexpand(True)
        self._toolbar_view.set_vexpand(True)
        self.append(self._toolbar_view)
        self._rows: dict[str, ServerRow] = {}
        self.add_css_class("server-sidebar")
        
        # Header bar — GNOME Files–like: new server top-left, centered title, menu top-right
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Create new server")
        add_btn.add_css_class("flat")
        add_btn.set_action_name("app.new-server")
        header.pack_start(add_btn)
        
        title = Gtk.Label(label="Servers")
        title.add_css_class("sidebar-header-title")
        header.set_title_widget(title)
        
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_tooltip_text("Main menu")
        menu_btn.add_css_class("flat")
        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About Hosty", "app.about")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)
        
        self._toolbar_view.add_top_bar(header)
        
        # Server list
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.add_css_class("navigation-sidebar")
        self._listbox.connect("row-selected", self._on_row_selected)
        
        self._scrolled.set_child(self._listbox)
        self._toolbar_view.set_content(self._scrolled)
        
        # Connect to server manager signals
        server_manager.connect('server-added', self._on_server_added)
        server_manager.connect('server-removed', self._on_server_removed)
        server_manager.connect('server-changed', self._on_server_changed)
        
        # Populate initial servers
        self._populate()
        
        # Right-click menu setup
        self._setup_context_menu()
    
    def _populate(self):
        """Populate the list with existing servers."""
        for server in self._server_manager.servers:
            self._add_row(server)
    
    def _add_row(self, server_info: ServerInfo):
        """Add a server row to the list."""
        row = ServerRow(server_info, self._server_manager)
        self._rows[server_info.id] = row
        self._listbox.append(row)
    
    def _on_row_selected(self, listbox, row):
        """Handle server selection."""
        if row and isinstance(row, ServerRow):
            self.emit('server-selected', row.server_info.id)
    
    def _on_server_added(self, manager, server_id):
        """Handle new server added."""
        info = manager.get_server(server_id)
        if info:
            self._add_row(info)
            # Select the new server
            row = self._rows.get(server_id)
            if row:
                self._listbox.select_row(row)
    
    def _on_server_removed(self, manager, server_id):
        """Handle server removed."""
        row = self._rows.pop(server_id, None)
        was_selected = row is not None and self._listbox.get_selected_row() is row
        if row:
            self._listbox.remove(row)

        if was_selected:
            self._listbox.select_row(None)
            self.emit('server-selected', "")
            return

        if not self._rows:
            self.emit('server-selected', "")
    
    def _on_server_changed(self, manager, server_id):
        """Handle server info changed."""
        row = self._rows.get(server_id)
        info = manager.get_server(server_id)
        if row and info:
            row.update_info(info)
    
    def select_server(self, server_id: str):
        """Programmatically select a server."""
        row = self._rows.get(server_id)
        if row:
            self._listbox.select_row(row)
    
    def _setup_context_menu(self):
        """Setup right-click context menu for server rows."""
        gesture = Gtk.GestureClick(button=3)  # Right click
        # Use released + correct coordinates so the menu is not under the pointer
        # (avoids accidental activation of the first item on touchpads).
        gesture.connect("released", self._on_right_click)
        self._listbox.add_controller(gesture)
    
    def _on_right_click(self, gesture, n_press, x, y):
        """Handle right-click on a server row."""
        if n_press != 1:
            return
        row = self._listbox.get_row_at_y(int(y))
        if not row or not isinstance(row, ServerRow):
            return
        
        self._listbox.select_row(row)
        server_info = row.server_info
        
        # Build popup menu — Rename not first (reduces mis-taps on first item)
        menu = Gio.Menu()
        menu.append("Change Icon", f"app.change-icon::{server_info.id}")
        menu.append("Rename…", f"app.rename-server::{server_info.id}")
        menu.append("Delete", f"app.delete-server::{server_info.id}")
        
        popover = Gtk.PopoverMenu(menu_model=menu)
        # Parent must match coordinate space of the gesture (listbox), not the row
        popover.set_parent(self._listbox)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()
