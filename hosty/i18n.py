"""Internationalization (i18n) support for Hosty."""

from __future__ import annotations

import builtins
import gettext
import os
import sys


def setup_gettext(localedir: str | None = None) -> None:
    """Initialize gettext and install _() into builtins."""
    if localedir is None:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = sys.prefix
        localedir = os.path.join(base, "share", "locale")

    try:
        gettext.bindtextdomain("hosty", localedir)
        gettext.textdomain("hosty")
    except Exception:
        pass

    builtins._ = gettext.gettext


setup_gettext()
