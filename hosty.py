#!/usr/bin/env python3
"""
Hosty - Minecraft Server Manager
A modern libadwaita application for creating, running,
and managing modded Minecraft servers.
"""

import os
import sys
from pathlib import Path

from hosty.daemon.entry import parse_daemon_args, run_headless
from hosty.factory import create_application
from hosty.shared.utils.constants import ensure_data_dirs


def _prepend_env_path(name: str, value: Path) -> None:
    value_text = str(value)
    current = os.environ.get(name)
    if current:
        os.environ[name] = value_text + os.pathsep + current
    else:
        os.environ[name] = value_text


def _configure_frozen_gtk_environment() -> None:
    """Point GTK/PyGObject at bundled runtime files in PyInstaller builds."""
    if not getattr(sys, "frozen", False):
        return

    exe_dir = Path(sys.executable).parent
    bundle_dir = Path(getattr(sys, "_MEIPASS", exe_dir))
    bundle_dirs = [bundle_dir, exe_dir, exe_dir / "_internal"]

    _prepend_env_path("PATH", bundle_dir)
    for candidate in bundle_dirs:
        if candidate.exists():
            _prepend_env_path("PATH", candidate)

    typelib_dir = next(
        (root / "lib" / "girepository-1.0" for root in bundle_dirs if (root / "lib" / "girepository-1.0").exists()),
        bundle_dir / "lib" / "girepository-1.0",
    )
    if typelib_dir.exists():
        _prepend_env_path("GI_TYPELIB_PATH", typelib_dir)

    share_dir = next(
        (root / "share" for root in bundle_dirs if (root / "share").exists()),
        bundle_dir / "share",
    )
    if share_dir.exists():
        _prepend_env_path("XDG_DATA_DIRS", share_dir)
        os.environ.setdefault("GTK_DATA_PREFIX", str(share_dir.parent))
        os.environ.setdefault("GTK_EXE_PREFIX", str(share_dir.parent))

    schemas_dir = share_dir / "glib-2.0" / "schemas"
    if schemas_dir.exists():
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", str(schemas_dir))

    pixbuf_root = next(
        (root / "lib" / "gdk-pixbuf-2.0" for root in bundle_dirs if (root / "lib" / "gdk-pixbuf-2.0").exists()),
        bundle_dir / "lib" / "gdk-pixbuf-2.0",
    )
    loaders_dir = pixbuf_root / "2.10.0" / "loaders"
    loaders_cache = pixbuf_root / "2.10.0" / "loaders.cache"
    if loaders_dir.exists():
        os.environ.setdefault("GDK_PIXBUF_MODULEDIR", str(loaders_dir))
    if loaders_cache.exists():
        os.environ.setdefault("GDK_PIXBUF_MODULE_FILE", str(loaders_cache))


def _configure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("io.github.sugarycandybar.Hosty")
    except Exception:
        pass

    from hosty.shared.utils.windows_instance import is_first_instance, signal_show

    if not is_first_instance():
        signal_show()
        sys.exit(0)


def _format_missing_gtk_message() -> str:
    if sys.platform == "win32":
        return (
            "GTK startup error: PyGObject is not installed for this Python. "
            "Install GTK4/libadwaita and PyGObject with MSYS2 UCRT64 or Conda, "
            "then run Hosty with that environment's Python."
        )

    return (
        "GTK startup error: PyGObject is not installed for this Python. "
        "Install GTK4/libadwaita and PyGObject for your system, then try again."
    )


def main():
    """Launch Hosty, either as the desktop app or as the headless daemon."""
    try:
        ensure_data_dirs()
        headless, _, argv = parse_daemon_args(sys.argv)
        if headless:
            return run_headless(sys.argv)

        _configure_windows_app_identity()
        _configure_frozen_gtk_environment()
        app = create_application()
        return app.run(argv)
    except KeyboardInterrupt:
        return 130
    except ModuleNotFoundError as exc:
        if exc.name == "gi":
            print(_format_missing_gtk_message(), file=sys.stderr)
            return 2
        print(f"Hosty startup error: {exc}", file=sys.stderr)
        return 1
    except NotImplementedError as exc:
        print(f"Hosty startup error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Hosty startup error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
