"""
Windows single-instance IPC using a named kernel event.

Allows a second instance to signal the first instance to show its window.
"""

from __future__ import annotations

import ctypes
import sys
from threading import Thread

kernel32 = ctypes.windll.kernel32

EVENT_NAME = "io.github.sugarycandybar.Hosty-SingleInstance"
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_FAILED = 0xFFFFFFFF
INFINITE = ctypes.c_uint32(-1).value

_event_handle = None
_watcher_thread = None


def _ensure_event_name() -> str:
    """Return a per-user event name so it works with per-user installs."""
    if sys.platform != "win32":
        return EVENT_NAME
    try:
        import os

        sid = os.environ.get("USERNAME", "default")
        return f"{EVENT_NAME}-{sid}"
    except Exception:
        return EVENT_NAME


def is_first_instance() -> bool:
    """Try to create a named event; return True if this is the first instance."""
    global _event_handle
    if sys.platform != "win32":
        return True
    try:
        name = _ensure_event_name()
        _event_handle = kernel32.CreateEventW(None, False, False, name)
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            if _event_handle:
                kernel32.CloseHandle(_event_handle)
                _event_handle = None
            return False
        return True
    except Exception:
        return True


def signal_show() -> None:
    """Signal the first instance to show its window, then exit."""
    if sys.platform != "win32":
        return
    try:
        name = _ensure_event_name()
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if handle:
            kernel32.SetEvent(handle)
            kernel32.CloseHandle(handle)
    except Exception:
        pass


def start_show_listener(show_callback) -> None:
    """Start a daemon thread that waits for the show event and calls the callback."""
    global _event_handle, _watcher_thread
    if sys.platform != "win32" or _event_handle is None:
        return

    from gi.repository import GLib

    def watcher():
        while True:
            result = kernel32.WaitForSingleObject(_event_handle, INFINITE)
            if result == WAIT_FAILED:
                break
            GLib.idle_add(show_callback)

    _watcher_thread = Thread(target=watcher, daemon=True)
    _watcher_thread.start()


def cleanup() -> None:
    """Close the event handle (called during app shutdown)."""
    global _event_handle, _watcher_thread
    if sys.platform != "win32":
        return
    if _event_handle:
        try:
            kernel32.CloseHandle(_event_handle)
        except Exception:
            pass
        _event_handle = None
    _watcher_thread = None
