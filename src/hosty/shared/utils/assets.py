"""Locate application data files in source, installed, and frozen builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_CSS_PROBE = "style.css"


def _source_root() -> Path:
    """Return the repository root when running from a source checkout."""
    # src/hosty/shared/utils/assets.py -> src/hosty/shared/utils -> ... -> repo
    return Path(__file__).resolve().parents[4]


def data_dirs() -> list[Path]:
    """Candidate directories containing packaged application data."""
    candidates: list[Path] = []

    env_dir = os.environ.get("HOSTY_PKGDATADIR")
    if env_dir:
        candidates.append(Path(env_dir))

    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend(
            [
                bundle_dir / "share" / "hosty",
                bundle_dir / "data",
                bundle_dir,
            ]
        )

    # Installed layout: <prefix>/share/hosty/hosty/shared/utils/assets.py
    candidates.append(Path(__file__).resolve().parents[3])
    # Source layout: <repo>/src/hosty/shared/utils/assets.py
    candidates.append(_source_root() / "data")

    return candidates


def data_dir() -> Path:
    """Return the first existing application data directory."""
    candidates = data_dirs()
    for candidate in candidates:
        if (candidate / _CSS_PROBE).is_file():
            return candidate
    return candidates[-1]


def asset_path(*parts: str) -> Path:
    """Return the path to an installed or source-tree data file."""
    return data_dir().joinpath(*parts)


def icon_theme_dir() -> Path | None:
    """Return a directory containing a ``hicolor`` icon tree, if one exists."""
    candidates: list[Path] = []

    env_dir = os.environ.get("HOSTY_ICON_DIR")
    if env_dir:
        candidates.append(Path(env_dir))

    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend(
            [
                bundle_dir / "share" / "icons",
                bundle_dir / "icons",
            ]
        )

    source_root = _source_root()
    candidates.extend(
        [
            source_root / "data" / "icons",
            # Installed layout: <prefix>/share
            Path(__file__).resolve().parents[4] / "icons",
        ]
    )

    for candidate in candidates:
        if (candidate / "hicolor").is_dir():
            return candidate
    return None
