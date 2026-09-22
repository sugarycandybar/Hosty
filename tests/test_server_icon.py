"""Tests for Minecraft multiplayer server icons."""

from pathlib import Path

import pytest
from PIL import Image

from hosty.shared.utils import image_utils


def _make_source(tmp_path: Path, name: str = "src.png", size=(200, 100), mode="RGB", fmt="PNG") -> Path:
    src = tmp_path / name
    color = (255, 0, 0, 255) if "A" in mode else (255, 0, 0)
    if mode == "P":
        img = Image.new("P", size)
    elif mode == "L":
        img = Image.new("L", size, 128)
    else:
        img = Image.new(mode, size, color)
    img.save(src, fmt)
    return src


def test_canonical_icon_constants():
    # The Minecraft spec is strict: exact name + exact size.
    assert image_utils.SERVER_ICON_FILENAME == "server-icon.png"
    assert image_utils.SERVER_ICON_SIZE == 64


def test_convert_various_formats_to_valid_icon(tmp_path: Path):
    """Every format the file chooser accepts must yield a real 64x64 PNG."""
    cases = [
        ("src.png", "PNG", "RGB", (200, 100)),
        ("src.jpg", "JPEG", "RGB", (100, 100)),
        ("src.webp", "WEBP", "RGB", (100, 100)),
        ("src.bmp", "BMP", "RGB", (100, 100)),
        ("src.gif", "GIF", "P", (100, 100)),
        ("src-gray.png", "PNG", "L", (100, 100)),
        ("src-alpha.png", "PNG", "RGBA", (100, 100)),
        ("src-tall.png", "PNG", "RGB", (100, 200)),
    ]
    for name, fmt, mode, size in cases:
        src = _make_source(tmp_path, name=name, size=size, mode=mode, fmt=fmt)
        out = tmp_path / f"out-{name}.png"
        image_utils.convert_to_png(str(src), str(out), size=64)
        assert image_utils.is_valid_server_icon(str(out)), f"{name} did not produce a valid icon"
        with Image.open(out) as im:
            assert im.size == (64, 64), name
            assert im.format == "PNG", name


def test_prepare_server_icon_writes_canonical_file(tmp_path: Path):
    src = _make_source(tmp_path, "logo.jpg", fmt="JPEG")
    server_dir = tmp_path / "server"
    result = image_utils.prepare_server_icon(str(src), str(server_dir))
    expected = server_dir / "server-icon.png"
    assert Path(result) == expected
    assert expected.exists()
    assert image_utils.is_valid_server_icon(str(expected))
    with Image.open(expected) as im:
        assert im.size == (64, 64)
        assert im.format == "PNG"


def test_prepare_server_icon_rejects_bad_input(tmp_path: Path):
    server_dir = tmp_path / "server"
    bogus = tmp_path / "bogus.png"
    bogus.write_text("not an image")
    with pytest.raises(Exception):
        image_utils.prepare_server_icon(str(bogus), str(server_dir))
    assert not (server_dir / "server-icon.png").exists()

    with pytest.raises(Exception):
        image_utils.prepare_server_icon(str(tmp_path / "missing.png"), str(server_dir))


def test_is_valid_server_icon_rejects_wrong_size_and_name(tmp_path: Path):
    # 128x128 (Hosty's old size) must be rejected
    big = tmp_path / "icon.png"
    Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(big, "PNG")
    assert not image_utils.is_valid_server_icon(str(big))

    # Wrong dimensions
    odd = tmp_path / "server-icon.png"
    Image.new("RGBA", (64, 65), (255, 0, 0, 255)).save(odd, "PNG")
    assert not image_utils.is_valid_server_icon(str(odd))

    # Renamed JPEG is not a real PNG
    fake = tmp_path / "fake.png"
    Image.new("RGB", (64, 64), (255, 0, 0)).save(fake, "JPEG")
    # Force .png extension with JPEG bytes
    data = fake.read_bytes()
    fake_png = tmp_path / "renamed.png"
    fake_png.write_bytes(data)
    assert not image_utils.is_valid_server_icon(str(fake_png))

    # Missing file
    assert not image_utils.is_valid_server_icon(str(tmp_path / "nope.png"))

    # Correct file passes
    good = tmp_path / "good.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(good, "PNG")
    assert image_utils.is_valid_server_icon(str(good))


def test_migrate_legacy_icon(tmp_path: Path):
    """Old installs wrote icon.png @128px; migration must produce server-icon.png @64px."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    legacy = server_dir / "icon.png"
    Image.new("RGBA", (128, 128), (0, 255, 0, 255)).save(legacy, "PNG")

    result = image_utils.migrate_legacy_server_icon(str(server_dir))
    canonical = server_dir / "server-icon.png"
    assert result is not None
    assert Path(result) == canonical
    assert image_utils.is_valid_server_icon(str(canonical))

    # Idempotent: second run keeps the valid canonical file
    result2 = image_utils.migrate_legacy_server_icon(str(server_dir))
    assert Path(result2) == canonical
    assert image_utils.is_valid_server_icon(str(canonical))


def test_migrate_prefers_existing_valid_canonical(tmp_path: Path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    canonical = server_dir / "server-icon.png"
    Image.new("RGBA", (64, 64), (0, 0, 255, 255)).save(canonical, "PNG")
    assert image_utils.migrate_legacy_server_icon(str(server_dir)) == str(canonical)


def test_dialogs_use_canonical_icon():
    """Both icon entry points must write server-icon.png at 64px, not icon.png at 128px."""
    picker_src = Path("hosty/gtk_ui/dialogs/icon_picker.py").read_text()
    create_src = Path("hosty/gtk_ui/dialogs/create_server.py").read_text()
    for src, name in ((picker_src, "icon_picker.py"), (create_src, "create_server.py")):
        assert "server-icon.png" in src or "SERVER_ICON_FILENAME" in src or "prepare_server_icon" in src, (
            f"{name} does not reference the canonical server-icon.png"
        )
        assert '"icon.png"' not in src and "'icon.png'" not in src, f"{name} still writes legacy icon.png"


def test_set_server_icon_normalizes_to_canonical(server_manager, tmp_path: Path):
    """set_server_icon must convert any source into server-icon.png 64x64."""
    info = server_manager.add_server("Icon Test", "1.20.1", loader_version="x", java_version=17)
    src = _make_source(tmp_path, "picked.webp", fmt="WEBP")
    server_manager.set_server_icon(info.id, str(src))
    updated = server_manager.get_server(info.id)
    assert updated is not None
    canonical = updated.server_dir / "server-icon.png"
    assert Path(updated.icon_path) == canonical
    assert image_utils.is_valid_server_icon(str(canonical))


def test_ensure_multiplayer_icon_migrates_legacy(server_manager, tmp_path: Path):
    """Legacy icon.png must be migrated on ensure (e.g. before server start)."""
    info = server_manager.add_server("Legacy", "1.20.1", loader_version="x", java_version=17)
    legacy = info.server_dir / "icon.png"
    Image.new("RGBA", (128, 128), (0, 255, 0, 255)).save(legacy, "PNG")
    # Simulate pre-fix metadata pointing at the legacy file
    info.icon_path = str(legacy)

    out = server_manager.ensure_multiplayer_icon(info.id)
    canonical = info.server_dir / "server-icon.png"
    assert out == str(canonical)
    assert image_utils.is_valid_server_icon(str(canonical))
    assert server_manager.get_server(info.id).icon_path == str(canonical)


def test_ensure_multiplayer_icon_no_icon_returns_none(server_manager):
    info = server_manager.add_server("NoIcon", "1.20.1", loader_version="x", java_version=17)
    assert server_manager.ensure_multiplayer_icon(info.id) is None


def test_manual_server_icon_is_kept(server_manager):
    """A valid manually placed server-icon.png must never be overwritten."""
    info = server_manager.add_server("Manual", "1.20.1", loader_version="x", java_version=17)
    canonical = info.server_dir / "server-icon.png"
    Image.new("RGBA", (64, 64), (0, 0, 255, 255)).save(canonical, "PNG")
    before = canonical.read_bytes()
    # Conflicting legacy file + stale metadata pointing at it
    legacy = info.server_dir / "icon.png"
    Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(legacy, "PNG")
    info.icon_path = str(legacy)

    out = server_manager.ensure_multiplayer_icon(info.id)
    assert out == str(canonical)
    assert canonical.read_bytes() == before
    assert server_manager.get_server(info.id).icon_path == str(canonical)


def test_migrate_keeps_valid_canonical_bytes(tmp_path: Path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    canonical = server_dir / "server-icon.png"
    Image.new("RGBA", (64, 64), (0, 0, 255, 255)).save(canonical, "PNG")
    before = canonical.read_bytes()
    Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(server_dir / "icon.png", "PNG")
    assert image_utils.migrate_legacy_server_icon(str(server_dir)) == str(canonical)
    assert canonical.read_bytes() == before
