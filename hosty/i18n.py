"""Internationalization (i18n) support for Hosty."""

from __future__ import annotations

import builtins
import gettext
import os
import sys

LANGUAGES: dict[str, str] = {
    "system": "System default",
    "en": "English",
    "pl": "Polski",
}

_localedir: str | None = None


def _compile_dev_mo() -> str | None:
    """Compile .po files to .mo for development if msgfmt is available."""
    po_dir = os.path.normpath(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "po")))
    mo_dir = os.path.join(po_dir, "mo")
    if not os.path.isdir(po_dir):
        return None
    has_mo = False
    for lang_code in LANGUAGES:
        if lang_code in ("system", "en"):
            continue
        po_path = os.path.join(po_dir, f"{lang_code}.po")
        mo_path = os.path.join(mo_dir, lang_code, "LC_MESSAGES", "hosty.mo")
        if os.path.isfile(mo_path):
            has_mo = True
            continue
        if os.path.isfile(po_path):
            try:
                os.makedirs(os.path.dirname(mo_path), exist_ok=True)
                import subprocess

                subprocess.run(["msgfmt", po_path, "-o", mo_path], check=True, capture_output=True)
                has_mo = True
            except Exception:
                pass
    return mo_dir if has_mo else None


def _default_localedir() -> str:
    """Return the default locale directory for the current environment."""
    env_dir = os.environ.get("HOSTY_LOCALEDIR")
    if env_dir:
        return env_dir
    if os.environ.get("FLATPAK_ID"):
        return "/app/share/locale"
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "share", "locale")
    dev_dir = _compile_dev_mo()
    if dev_dir:
        return dev_dir
    return os.path.join(sys.prefix, "share", "locale")


def setup_gettext(localedir: str | None = None) -> None:
    """Initialize gettext and install _() into builtins."""
    global _localedir
    if localedir is None:
        localedir = _default_localedir()
    _localedir = localedir

    try:
        gettext.bindtextdomain("hosty", localedir)
        gettext.textdomain("hosty")
    except Exception:
        pass

    builtins._ = gettext.gettext


def set_language(lang_code: str) -> None:
    """Switch the active translation at runtime."""
    if lang_code == "system" or not lang_code:
        os.environ.pop("LANGUAGE", None)
        try:
            gettext.bindtextdomain("hosty", _localedir)
            gettext.textdomain("hosty")
        except Exception:
            pass
        builtins._ = gettext.gettext
    else:
        try:
            translation = gettext.translation("hosty", _localedir, languages=[lang_code])
            builtins._ = translation.gettext
            os.environ["LANGUAGE"] = lang_code
        except Exception:
            os.environ.pop("LANGUAGE", None)
            try:
                gettext.bindtextdomain("hosty", _localedir)
                gettext.textdomain("hosty")
            except Exception:
                pass
            builtins._ = gettext.gettext


setup_gettext()
