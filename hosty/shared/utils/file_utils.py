"""
Filesystem helpers for safe writes.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write text to path atomically: temp file in the same dir + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Write bytes to path atomically: temp file in the same dir + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | Path, data) -> None:
    """Serialize data as JSON and write it atomically."""
    atomic_write_text(path, json.dumps(data, indent=2))
