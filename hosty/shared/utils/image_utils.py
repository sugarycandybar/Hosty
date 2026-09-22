"""
Image utility functions for Hosty.
Handles image cropping, conversion, and loading for GTK display.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

SERVER_ICON_FILENAME = "server-icon.png"
SERVER_ICON_SIZE = 64

LEGACY_ICON_FILENAME = "icon.png"
LEGACY_ICON_SIZE = 128

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, Gtk
except ImportError:
    gi = None
    GdkPixbuf = None
    Gdk = None
    Gtk = None


def crop_to_square(input_path: str, x: int, y: int, size: int) -> Image.Image:
    """Crop an image to a square region."""
    img = Image.open(input_path)
    img = img.convert("RGBA")
    cropped = img.crop((x, y, x + size, y + size))
    return cropped


def convert_to_png(input_path: str, output_path: str, size: int = SERVER_ICON_SIZE, crop_box: tuple = None) -> str:
    """
    Convert an image to PNG format, optionally cropping and resizing.

    Args:
        input_path: Path to the source image.
        output_path: Path to save the PNG.
        size: Output size (square). Defaults to 64 (Minecraft server-icon spec).
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


def is_valid_server_icon(path: str) -> bool:
    """Check whether a file meets Minecraft's multiplayer icon spec.

    Requires an existing real PNG that is exactly 64x64 pixels.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return False
        with Image.open(p) as img:
            if img.format != "PNG":
                return False
            if img.size != (SERVER_ICON_SIZE, SERVER_ICON_SIZE):
                return False
        return True
    except Exception:
        return False


def prepare_server_icon(source_path: str, server_dir: str) -> str:
    """Convert any supported image into a valid multiplayer icon.

    Writes ``server-icon.png`` (64x64 PNG) into ``server_dir`` and removes
    the legacy ``icon.png`` / preview leftovers so the two cannot diverge.

    Raises the underlying PIL/IO error for bad inputs (missing file,
    unidentified image, unreadable data) -- callers should surface this
    instead of silently keeping a broken icon.
    """
    from pathlib import Path as _Path

    src = _Path(source_path)
    dest_dir = _Path(server_dir)
    if not src.is_file():
        raise FileNotFoundError(f"Icon source not found: {source_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / SERVER_ICON_FILENAME

    try:
        same_file = dest.exists() and src.resolve() == dest.resolve()
    except Exception:
        same_file = False

    if same_file:
        # Source already is the canonical file: normalize in place via temp.
        tmp = dest_dir / (SERVER_ICON_FILENAME + ".tmp")
        convert_to_png(str(src), str(tmp), size=SERVER_ICON_SIZE)
        if not is_valid_server_icon(str(tmp)):
            tmp.unlink(missing_ok=True)
            raise ValueError(f"Could not normalize icon: {source_path}")
        tmp.replace(dest)
    else:
        convert_to_png(str(src), str(dest), size=SERVER_ICON_SIZE)
        if not is_valid_server_icon(str(dest)):
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            raise ValueError(f"Could not convert to a valid server icon: {source_path}")

    # Clean up legacy / preview files so only the canonical icon remains.
    for stale in (LEGACY_ICON_FILENAME, "icon_preview.png"):
        try:
            stale_path = dest_dir / stale
            if stale_path != dest and stale_path.exists():
                stale_path.unlink()
        except Exception:
            pass

    return str(dest)


def migrate_legacy_server_icon(server_dir: str) -> str | None:
    """Ensure ``server-icon.png`` exists for a server dir.

    - If a valid canonical icon already exists, return it.
    - Else if legacy ``icon.png`` exists, convert it to the canonical file.
    - Else return None (nothing to migrate).

    Returns the canonical path, or None.
    """
    from pathlib import Path as _Path

    dest_dir = _Path(server_dir)
    canonical = dest_dir / SERVER_ICON_FILENAME
    if is_valid_server_icon(str(canonical)):
        return str(canonical)
    legacy = dest_dir / LEGACY_ICON_FILENAME
    if legacy.is_file():
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            convert_to_png(str(legacy), str(canonical), size=SERVER_ICON_SIZE)
            if is_valid_server_icon(str(canonical)):
                return str(canonical)
        except Exception:
            pass
        # Remove a half-written/invalid canonical file, keep legacy for UI.
        try:
            if canonical.exists() and not is_valid_server_icon(str(canonical)):
                canonical.unlink()
        except Exception:
            pass
        return None
    return str(canonical) if is_valid_server_icon(str(canonical)) else None


def load_pixbuf(path: str, size: int = 128) -> GdkPixbuf.Pixbuf | None:
    """Load an image file as a GdkPixbuf at the given size."""
    if GdkPixbuf is None:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
        return pixbuf
    except Exception:
        return None


def create_texture_from_file(path: str, size: int = 128) -> Gdk.Texture | None:
    """Load an image file as a Gdk.Texture."""
    if Gdk is None:
        return None
    try:
        pixbuf = load_pixbuf(path, size)
        if pixbuf:
            return Gdk.Texture.new_for_pixbuf(pixbuf)
    except Exception:
        pass
    return None


def get_default_server_icon_pixbuf(size: int = 48) -> GdkPixbuf.Pixbuf | None:
    """Create a default server icon (simple colored square)."""
    if GdkPixbuf is None:
        return None
    # Create a simple colored pixbuf as default
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    # Fill with a nice purple/blue color
    pixbuf.fill(0x7C6BF0FF)
    return pixbuf
