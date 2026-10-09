"""Internationalization (i18n) support for Hosty."""

from __future__ import annotations

import builtins
import gettext
import locale
import os
import sys
from pathlib import Path

GETTEXT_DOMAIN = "hosty"

# Display names for known languages. Unknown codes fall back to the code itself.
_LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "en": "English",
    "pl": "Polski",
}

# Module-level language map kept for backwards compatibility
# (preferences dialog imports LANGUAGES). Refreshed by setup_gettext().
LANGUAGES: dict[str, str] = {
    "system": "System default",
    "en": "English",
    "pl": "Polski",
}

_localedir: str | None = None
_current_translation: gettext.NullTranslations | gettext.GNUTranslations = gettext.NullTranslations()


def _repo_po_dir() -> Path | None:
    """Return the source-tree po/ dir when running from a checkout."""
    try:
        here = Path(__file__).resolve()
    except Exception:
        return None
    for parent in [here.parent, *here.parents]:
        candidate = parent / "po"
        if (candidate / "LINGUAS").is_file():
            return candidate
    # Fallback: <repo>/po relative to this file (hosty/i18n.py -> repo/po)
    fallback = here.parent.parent / "po"
    if (fallback / "LINGUAS").is_file():
        return fallback
    return None


def _compile_dev_mo() -> str | None:
    """Compile .po files to .mo for development if msgfmt is available."""
    po_dir = _repo_po_dir()
    if po_dir is None or not po_dir.is_dir():
        return None
    mo_dir = po_dir / "mo"
    # Discover languages from po/*.po so we don't depend on LANGUAGES.
    try:
        po_files = sorted(po_dir.glob("*.po"))
    except Exception:
        return None
    has_mo = False
    for po_path in po_files:
        lang_code = po_path.stem
        if lang_code == "hosty" or lang_code.startswith("hosty"):
            continue
        mo_path = mo_dir / lang_code / "LC_MESSAGES" / f"{GETTEXT_DOMAIN}.mo"
        if mo_path.is_file():
            has_mo = True
            # Recompile when the .po is newer than the .mo (stale dev build).
            try:
                if mo_path.stat().st_mtime >= po_path.stat().st_mtime:
                    continue
            except Exception:
                continue
        try:
            mo_path.parent.mkdir(parents=True, exist_ok=True)
            import subprocess

            subprocess.run(["msgfmt", str(po_path), "-o", str(mo_path)], check=True, capture_output=True)
            has_mo = True
        except Exception:
            pass
    return str(mo_dir) if has_mo else None


def _candidate_localedirs() -> list[str]:
    """All directories that may contain share/locale, in priority order."""
    candidates: list[str] = []
    env_dir = os.environ.get("HOSTY_LOCALEDIR")
    if env_dir:
        candidates.append(env_dir)
    if os.environ.get("FLATPAK_ID"):
        candidates.append("/app/share/locale")
    if sys.platform == "win32":
        # PyInstaller: sys._MEIPASS (one-dir: exe adjacent, one-file: temp bundle)
        # plus exe dir and exe dir/_internal for bundled share/locale.
        try:
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(os.path.join(str(meipass), "share", "locale"))
        except Exception:
            pass
        try:
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""
            if exe_dir:
                candidates.append(os.path.join(exe_dir, "share", "locale"))
                candidates.append(os.path.join(exe_dir, "_internal", "share", "locale"))
        except Exception:
            pass
        # MSYS2/Conda layout: <prefix>/share/locale
        for prefix in (sys.prefix, sys.base_prefix, os.path.dirname(sys.executable)):
            try:
                if prefix:
                    candidates.append(os.path.join(prefix, "share", "locale"))
            except Exception:
                continue
        # MSYS2 UCRT64 system prefix when running under a different Python
        for p in (r"C:\\msys64\\ucrt64\\share\\locale", r"C:\\msys64\\mingw64\\share\\locale"):
            candidates.append(p)
    # Deduplicate while preserving order, keeping only existing dirs later.
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _default_localedir() -> str:
    """Return the default locale directory for the current environment."""
    for candidate in _candidate_localedirs():
        try:
            if os.path.isdir(candidate):
                # Accept env/Flatpak/frozen candidates even if empty;
                # they are the correct install location.
                if (
                    candidate == os.environ.get("HOSTY_LOCALEDIR")
                    or os.environ.get("FLATPAK_ID")
                    or (sys.platform == "win32" and getattr(sys, "frozen", False))
                ):
                    return candidate
        except Exception:
            continue
    dev_dir = _compile_dev_mo()
    if dev_dir:
        return dev_dir
    for candidate in _candidate_localedirs():
        try:
            if os.path.isdir(candidate):
                return candidate
        except Exception:
            continue
    return os.path.join(sys.prefix, "share", "locale")


def _expand_language_variants(code: str) -> list[str]:
    """Expand 'pl_PL.UTF-8' or 'pl-PL' to ['pl_PL', 'pl']. Deduplicated."""
    code = (code or "").strip().replace("-", "_")
    if not code:
        return []
    # Strip encoding/modifier: pl_PL.UTF-8@euro -> pl_PL
    code = code.split("@")[0].split(".")[0]
    variants = [code]
    if "_" in code:
        variants.append(code.split("_")[0])
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _windows_system_languages() -> list[str]:
    """Best-effort Windows UI language detection (e.g. ['pl_PL', 'pl'])."""
    langs: list[str] = []
    # 1. Python locale detection (reads Windows settings on win32)
    try:
        default = locale.getdefaultlocale()[0] if hasattr(locale, "getdefaultlocale") else None
        for variant in _expand_language_variants(str(default or "")):
            if variant not in langs:
                langs.append(variant)
    except Exception:
        pass
    # 2. Win32 API: GetUserDefaultLocaleName -> 'pl-PL'
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        buf_len = 85
        buf = ctypes.create_unicode_buffer(buf_len)
        if kernel32.GetUserDefaultLocaleName(buf, buf_len):
            for variant in _expand_language_variants(buf.value):
                if variant not in langs:
                    langs.append(variant)
    except Exception:
        pass
    # 3. UI language LCID -> locale name via locale.windows_locale
    try:
        import ctypes

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        win_locales = getattr(locale, "windows_locale", {})
        name = win_locales.get(int(lcid))
        for variant in _expand_language_variants(str(name or "")):
            if variant not in langs:
                langs.append(variant)
    except Exception:
        pass
    return langs


def _detect_system_languages() -> list[str]:
    """Ordered list of gettext language codes for 'System default'."""
    langs: list[str] = []
    # Explicit env vars first (honours MSYS2/Linux and LANGUAGE priority)
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        try:
            raw = os.environ.get(var, "")
        except Exception:
            raw = ""
        if not raw:
            continue
        # LANGUAGE is colon-separated with priority order
        parts = raw.replace(";", ":").split(":")
        for part in parts:
            for variant in _expand_language_variants(part):
                if variant not in langs:
                    langs.append(variant)
        if var == "LANGUAGE" and langs:
            break
    if sys.platform == "win32":
        for variant in _windows_system_languages():
            if variant not in langs:
                langs.append(variant)
    else:
        # POSIX fallback: locale.getdefaultlocale()
        try:
            default = locale.getdefaultlocale()[0] if hasattr(locale, "getdefaultlocale") else None
            for variant in _expand_language_variants(str(default or "")):
                if variant not in langs:
                    langs.append(variant)
        except Exception:
            pass
    # Always allow plain English fallback lookup
    if "en" not in langs:
        langs.append("en")
    return langs


def _available_language_codes(localedir: str | None = None) -> list[str]:
    """Codes with an installed or source-tree translation. Always includes 'en'."""
    codes: set[str] = {"en"}
    # 1. Installed .mo files
    dirs = [localedir] if localedir else _candidate_localedirs()
    if not localedir:
        # Also consider the resolved default dir
        try:
            dirs.append(_default_localedir())
        except Exception:
            pass
    for base in dirs:
        try:
            if not base or not os.path.isdir(base):
                continue
            for entry in os.listdir(base):
                mo = os.path.join(base, entry, "LC_MESSAGES", f"{GETTEXT_DOMAIN}.mo")
                if os.path.isfile(mo):
                    codes.add(entry)
        except Exception:
            continue
    # 2. Source-tree po/*.po (dev checkouts, translators)
    po_dir = _repo_po_dir()
    if po_dir is not None:
        try:
            for po_file in po_dir.glob("*.po"):
                name = po_file.stem
                if name and not name.startswith("hosty"):
                    codes.add(name)
        except Exception:
            pass
    # 3. LINGUAS file (authoritative list)
    if po_dir is not None:
        try:
            linguas = (po_dir / "LINGUAS").read_text(encoding="utf-8", errors="replace")
            for line in linguas.splitlines():
                # Strip comments: everything after '#' is ignored
                line = line.split("#", 1)[0]
                for token in line.replace(",", " ").split():
                    token = token.strip()
                    if token:
                        codes.add(token)
        except Exception:
            pass
    return sorted(codes)


def _display_name(code: str) -> str:
    if code in _LANGUAGE_DISPLAY_NAMES:
        return _LANGUAGE_DISPLAY_NAMES[code]
    try:
        import locale as _locale

        # Best effort: language name in English via locale data is not
        # portable, so fall back to the code itself.
        _ = _locale
    except Exception:
        pass
    return code


def get_available_languages(localedir: str | None = None) -> dict[str, str]:
    """Ordered {code: display} map for the preferences dialog."""
    try:
        base = localedir or _localedir or _default_localedir()
    except Exception:
        base = None
    codes = _available_language_codes(base)
    result: dict[str, str] = {"system": "System default"}
    for code in codes:
        result[code] = _display_name(code)
    return result


def _refresh_languages() -> None:
    """Refresh the backwards-compatible LANGUAGES dict in place."""
    try:
        fresh = get_available_languages()
        LANGUAGES.clear()
        LANGUAGES.update(fresh)
    except Exception:
        pass


def _install_translation(translation: gettext.NullTranslations) -> None:
    global _current_translation
    _current_translation = translation
    builtins._ = translation.gettext  # type: ignore[attr-defined]
    ngettext_func = getattr(translation, "ngettext", None)
    if callable(ngettext_func):
        builtins.ngettext = ngettext_func  # type: ignore[attr-defined]
    else:
        fallback = gettext.NullTranslations()
        builtins.ngettext = fallback.ngettext  # type: ignore[attr-defined]
    # Also expose module-level names for `from hosty.i18n import _/ngettext`
    globals()["_"] = builtins._
    globals()["ngettext"] = builtins.ngettext


def setup_gettext(localedir: str | None = None) -> None:
    """Initialize gettext and install _() and ngettext() into builtins."""
    global _localedir
    if localedir is None:
        localedir = _default_localedir()
    _localedir = localedir

    try:
        gettext.bindtextdomain(GETTEXT_DOMAIN, localedir)
        gettext.textdomain(GETTEXT_DOMAIN)
    except Exception:
        pass

    # Try the system language immediately so a fresh start (before any
    # set_language call) already shows translations, including on Windows
    # where bare gettext.gettext() would otherwise stay English.
    try:
        system_langs = _detect_system_languages()
        translation = gettext.translation(GETTEXT_DOMAIN, localedir, languages=system_langs, fallback=True)
    except Exception:
        translation = gettext.NullTranslations()
    _install_translation(translation)
    _refresh_languages()


def set_language(lang_code: str) -> None:
    """Switch the active translation at runtime."""
    global _localedir
    if _localedir is None:
        try:
            _localedir = _default_localedir()
        except Exception:
            _localedir = None
    if lang_code == "system" or not lang_code:
        try:
            os.environ.pop("LANGUAGE", None)
        except Exception:
            pass
        try:
            system_langs = _detect_system_languages()
            translation = (
                gettext.translation(GETTEXT_DOMAIN, _localedir, languages=system_langs, fallback=True)
                if _localedir
                else gettext.NullTranslations()
            )
        except Exception:
            translation = gettext.NullTranslations()
        try:
            if _localedir:
                gettext.bindtextdomain(GETTEXT_DOMAIN, _localedir)
                gettext.textdomain(GETTEXT_DOMAIN)
        except Exception:
            pass
        _install_translation(translation)
    else:
        # Accept 'pl', 'pl_PL', 'pl-PL' etc.
        candidates: list[str] = []
        for variant in _expand_language_variants(lang_code):
            if variant not in candidates:
                candidates.append(variant)
        if lang_code not in candidates:
            candidates.append(lang_code)
        try:
            translation = (
                gettext.translation(GETTEXT_DOMAIN, _localedir, languages=candidates, fallback=True)
                if _localedir
                else gettext.NullTranslations()
            )
            # gettext with fallback=True returns NullTranslations when no .mo
            # was found; keep it (English fallback) but still record LANGUAGE.
            if isinstance(translation, gettext.NullTranslations):
                try:
                    mo_found = gettext.find(GETTEXT_DOMAIN, _localedir, languages=candidates) if _localedir else None
                except Exception:
                    mo_found = None
                if not mo_found:
                    translation = gettext.NullTranslations()
            try:
                os.environ["LANGUAGE"] = lang_code
            except Exception:
                pass
        except Exception:
            try:
                os.environ.pop("LANGUAGE", None)
            except Exception:
                pass
            translation = gettext.NullTranslations()
        try:
            if _localedir:
                gettext.bindtextdomain(GETTEXT_DOMAIN, _localedir)
                gettext.textdomain(GETTEXT_DOMAIN)
        except Exception:
            pass
        _install_translation(translation)
    _refresh_languages()


# Module-level conveniences: `from hosty.i18n import _, ngettext`
try:
    _ = builtins._  # type: ignore[attr-defined]
except Exception:
    _ = gettext.gettext  # type: ignore[no-redef]

try:
    ngettext = builtins.ngettext  # type: ignore[attr-defined]
except Exception:
    ngettext = gettext.NullTranslations().ngettext  # type: ignore[no-redef]


setup_gettext()
