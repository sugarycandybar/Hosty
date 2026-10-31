"""Early process setup that must run before the rest of Hosty is imported.

This module deliberately avoids importing anything from the ``hosty`` package
so it is safe to call from both the installed launcher and the Windows/
PyInstaller entry point before ``constants`` is imported.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_environment() -> None:
    """Set up the per-user data directory before constants are imported."""
    data_dir = os.environ.get("HOSTY_DATA_DIR")
    if not data_dir:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if not xdg_data_home:
            xdg_data_home = os.path.join(os.path.expanduser("~"), ".local", "share")
        data_dir = os.path.join(xdg_data_home, "hosty")
        os.environ["HOSTY_DATA_DIR"] = data_dir

    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"Warning: Could not create data directory {data_dir}: {exc}", file=sys.stderr)
