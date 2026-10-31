#!/usr/bin/env python3
"""Install the ``hosty`` Python package without bytecode caches.

Meson's ``install_subdir`` can only exclude top-level directories, so nested
``__pycache__`` folders from a development checkout would otherwise be
installed as well. This script copies the package tree while skipping caches
and build-only templates.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_EXCLUDED_NAMES = {"__pycache__"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
_EXCLUDED_FILES = {"build_info.py.in"}


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in _EXCLUDED_NAMES or name in _EXCLUDED_FILES}
    ignored.update(name for name in names if Path(name).suffix in _EXCLUDED_SUFFIXES)
    return ignored


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: install_package.py SOURCE_DIR INSTALL_SUBDIR", file=sys.stderr)
        return 2

    source = Path(sys.argv[1]).resolve()
    prefix = Path(os.environ["MESON_INSTALL_DESTDIR_PREFIX"])
    destination = prefix / sys.argv[2] / "hosty"

    shutil.copytree(source, destination, ignore=_ignore, dirs_exist_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
