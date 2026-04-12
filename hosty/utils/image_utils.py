"""
Image utility functions for Hosty.
Handles image cropping, conversion, and loading for GTK display.
"""
from pathlib import Path
from PIL import Image
import io
import tempfile
import sys

try:
    if sys.platform != "win32":
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Gdk', '4.0')
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, Gdk, Gtk
except ImportError:
    pass


def crop_to_square(input_path: str, x: int, y: int, size: int) -> Image.Image:
    """Crop an image to a square region."""
    img = Image.open(input_path)
    img = img.convert("RGBA")
    cropped = img.crop((x, y, x + size, y + size))
    return cropped


def convert_to_png(input_path: str, output_path: str, size: int = 128,
                   crop_box: tuple = None) -> str:
    """
    Convert an image to PNG format, optionally cropping and resizing.
    
    Args:
        input_path: Path to the source image.
        output_path: Path to save the PNG.
        size: Output size (square).
        crop_box: Optional (x, y, width, height) crop region.
    
    Returns:
        The output_path.
    """
    img = Image.open(input_path)
    img = img.convert("RGBA")
    
    if crop_box:
        x, y, w, h = crop_box
        img = img.crop((x, y, x + w, y + h))
    else:
        # Auto-crop to center square
        w, h = img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        img = img.crop((left, top, left + min_dim, top + min_dim))
    
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    img.save(output_path, "PNG")
    return output_path


def load_pixbuf(path: str, size: int = 128) -> GdkPixbuf.Pixbuf:
    """Load an image file as a GdkPixbuf at the given size."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), size, size, True
        )
        return pixbuf
    except Exception:
        return None


def create_texture_from_file(path: str, size: int = 128) -> Gdk.Texture:
    """Load an image file as a Gdk.Texture."""
    try:
        pixbuf = load_pixbuf(path, size)
        if pixbuf:
            return Gdk.Texture.new_for_pixbuf(pixbuf)
    except Exception:
        pass
    return None


def get_default_server_icon_pixbuf(size: int = 48) -> GdkPixbuf.Pixbuf:
    """Create a default server icon (simple colored square)."""
    # Create a simple colored pixbuf as default
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    # Fill with a nice purple/blue color
    pixbuf.fill(0x7c6bf0ff)
    return pixbuf
