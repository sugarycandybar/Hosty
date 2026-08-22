"""Static file serving for the daemon's management web UI."""

from __future__ import annotations

import mimetypes
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def resolve_static(request_path: str) -> tuple[Path, str] | None:
    """Map a request path to a static file, guarding against traversal.

    ``/`` maps to ``index.html``; a missing path falls back to ``index.html``
    so the browser UI can reload on any route.
    """
    relative = request_path.strip("/").replace("\\", "/")
    if not relative:
        return _serve_file("index.html")

    parts = [part for part in relative.split("/") if part not in ("", ".", "..")]
    if not parts:
        return _serve_file("index.html")

    safe = Path(*parts)
    target = (STATIC_DIR / safe).resolve()
    if not target.is_relative_to(STATIC_DIR):
        return None

    if target.is_dir() or not target.is_file():
        return _serve_file("index.html")

    guess = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    content_type = _CONTENT_TYPES.get(target.suffix.lower(), guess)
    return target, content_type


def _serve_file(name: str) -> tuple[Path, str] | None:
    path = STATIC_DIR / name
    if not path.is_file():
        return None
    content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return path, content_type
