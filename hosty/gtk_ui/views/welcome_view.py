"""
WelcomeView - Empty state shown when no server is selected.
Includes its own Adw.HeaderBar with proper window controls.
"""
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Adw, Gdk, GdkPixbuf

from hosty.shared.utils.constants import APP_ID


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

        # Use an explicit centered layout so the icon is rendered at a fixed size
        # without extra scaling from StatusPage internals.
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        content.set_vexpand(True)
        content.set_hexpand(True)

        icon = Gtk.Image()
        icon.set_pixel_size(96)
        icon_path = Path(__file__).resolve().parents[3] / "packaging" / "linux" / f"{APP_ID}-symbolic.svg"
        if icon_path.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), 128, 128, True)
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                icon.set_from_paintable(texture)
            except Exception:
                icon.set_from_icon_name(f"{APP_ID}-symbolic")
        else:
            icon.set_from_icon_name(f"{APP_ID}-symbolic")
        content.append(icon)

        title = Gtk.Label(label="Welcome to Hosty")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        content.append(title)

        description = Gtk.Label(
            label=(
                "Create and manage your Fabric Minecraft servers\n"
                "with an easy-to-use interface."
            )
        )
        description.set_justify(Gtk.Justification.CENTER)
        description.set_wrap(True)
        description.set_halign(Gtk.Align.CENTER)
        content.append(description)

        # Create server button — use standard Adwaita suggested-action
        btn = Gtk.Button(label="Create Server")
        btn.set_halign(Gtk.Align.CENTER)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_action_name("app.new-server")

        content.append(btn)
        self._toolbar_view.set_content(content)
