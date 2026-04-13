"""
IconPickerDialog - Image selector with crop-to-square and PNG conversion.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GdkPixbuf, GLib, GObject, Gio

from hosty.shared.utils.image_utils import convert_to_png, load_pixbuf
from pathlib import Path


class IconPickerDialog(Adw.Dialog):
    """Dialog for selecting, cropping, and setting a server icon."""
    
    __gsignals__ = {
        'icon-selected': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }
    
    def __init__(self, server_id: str, server_dir: str):
        super().__init__()
        self._server_id = server_id
        self._server_dir = Path(server_dir)
        self._source_path = None
        self._pixbuf = None
        
        # Crop region (relative 0-1)
        self._crop_x = 0.0
        self._crop_y = 0.0
        self._crop_size = 1.0
        
        self.set_title("Change Server Icon")
        self.set_content_width(450)
        self.set_content_height(500)
        
        toolbar = Adw.ToolbarView()
        
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)
        
        self._apply_btn = Gtk.Button(label="Apply")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.set_sensitive(False)
        self._apply_btn.connect("clicked", self._on_apply)
        header.pack_end(self._apply_btn)
        
        toolbar.add_top_bar(header)
        
        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        
        # Preview area
        preview_frame = Gtk.Frame()
        preview_frame.set_halign(Gtk.Align.CENTER)
        
        self._preview = Gtk.Picture()
        self._preview.set_size_request(200, 200)
        self._preview.set_content_fit(Gtk.ContentFit.COVER)
        preview_frame.set_child(self._preview)
        content.append(preview_frame)
        
        # Result preview (small avatar)
        result_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        result_box.set_halign(Gtk.Align.CENTER)
        
        result_label = Gtk.Label(label="Preview:")
        result_label.add_css_class("dim-label")
        result_box.append(result_label)
        
        self._result_avatar = Adw.Avatar(size=48, text="?")
        result_box.append(self._result_avatar)
        
        content.append(result_box)
        
        # File chooser button
        choose_btn = Gtk.Button(label="Choose Image…")
        choose_btn.add_css_class("pill")
        choose_btn.set_halign(Gtk.Align.CENTER)
        choose_btn.connect("clicked", self._on_choose_image)
        content.append(choose_btn)
        
        # Info
        info_label = Gtk.Label(
            label="Select a PNG, JPG, or WebP image.\nIt will be automatically cropped to a square and resized."
        )
        info_label.add_css_class("dim-label")
        info_label.set_halign(Gtk.Align.CENTER)
        info_label.set_justify(Gtk.Justification.CENTER)
        content.append(info_label)
        
        toolbar.set_content(content)
        self.set_child(toolbar)
    
    def _on_choose_image(self, button):
        """Open file chooser for image selection."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Server Icon")
        
        # File filters
        filter_images = Gtk.FileFilter()
        filter_images.set_name("Images")
        filter_images.add_mime_type("image/png")
        filter_images.add_mime_type("image/jpeg")
        filter_images.add_mime_type("image/webp")
        filter_images.add_mime_type("image/bmp")
        filter_images.add_mime_type("image/gif")
        
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        filter_list.append(filter_images)
        dialog.set_filters(filter_list)
        dialog.set_default_filter(filter_images)
        
        dialog.open(
            self.get_root(),
            None,
            self._on_file_chosen,
        )
    
    def _on_file_chosen(self, dialog, result):
        """Handle file chooser result."""
        try:
            file = dialog.open_finish(result)
            if file:
                self._source_path = file.get_path()
                self._load_preview()
        except GLib.Error:
            pass
    
    def _load_preview(self):
        """Load and display the selected image."""
        if not self._source_path:
            return
        
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(self._source_path)
            
            # Show preview
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            self._preview.set_paintable(texture)
            
            # Generate cropped preview
            self._generate_cropped_preview()
            
            self._apply_btn.set_sensitive(True)
            
        except Exception as e:
            print(f"Failed to load image: {e}")
    
    def _generate_cropped_preview(self):
        """Generate a small cropped preview avatar."""
        try:
            # Use PIL to do the crop
            output_path = self._server_dir / "icon_preview.png"
            self._server_dir.mkdir(parents=True, exist_ok=True)
            
            convert_to_png(
                self._source_path,
                str(output_path),
                size=128,
            )
            
            pixbuf = load_pixbuf(str(output_path), 48)
            if pixbuf:
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                self._result_avatar.set_custom_image(texture)
            
        except Exception as e:
            print(f"Failed to generate preview: {e}")
    
    def _on_apply(self, button):
        """Apply the selected icon."""
        if not self._source_path:
            return
        
        try:
            output_path = self._server_dir / "icon.png"
            self._server_dir.mkdir(parents=True, exist_ok=True)
            
            convert_to_png(
                self._source_path,
                str(output_path),
                size=128,
            )
            
            # Clean up preview
            preview_path = self._server_dir / "icon_preview.png"
            preview_path.unlink(missing_ok=True)
            
            self.emit('icon-selected', str(output_path))
            self.close()
            
        except Exception as e:
            print(f"Failed to save icon: {e}")
