"""Toolkit-agnostic event primitives with optional main-thread dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DispatcherFunc = Callable[[Callable[..., Any], Any], Any]


def _default_dispatch(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


_main_thread_dispatcher: DispatcherFunc = _default_dispatch


def set_main_thread_dispatcher(dispatcher: DispatcherFunc | None) -> None:
    """Set dispatcher used for scheduling callbacks on the UI/main thread."""
    global _main_thread_dispatcher
    _main_thread_dispatcher = dispatcher or _default_dispatch


def dispatch_on_main_thread(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run callback through the configured main-thread dispatcher."""
    return _main_thread_dispatcher(callback, *args, **kwargs)


class EventEmitter:
    """Small signal API compatible with connect/emit/disconnect usage in UI code."""

    def __init__(self) -> None:
        self._listeners: dict[str, dict[int, Callable[..., Any]]] = {}
        self._listener_index: dict[int, str] = {}
        self._next_handler_id = 1

    def connect(self, signal_name: str, callback: Callable[..., Any]) -> int:
        handlers = self._listeners.setdefault(signal_name, {})
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        handlers[handler_id] = callback
        self._listener_index[handler_id] = signal_name
        return handler_id

    def disconnect(self, handler_id: int) -> bool:
        signal_name = self._listener_index.pop(handler_id, None)
        if signal_name is None:
            return False
        handlers = self._listeners.get(signal_name)
        if not handlers:
            return False
        handlers.pop(handler_id, None)
        if not handlers:
            self._listeners.pop(signal_name, None)
        return True

    def emit(self, signal_name: str, *args: Any) -> None:
        handlers = self._listeners.get(signal_name, {})
        for callback in list(handlers.values()):
            try:
                callback(self, *args)
            except Exception:
                # Keep signal delivery resilient if one handler fails.
                continue

    def emit_on_main_thread(self, signal_name: str, *args: Any) -> None:
        dispatch_on_main_thread(self.emit, signal_name, *args)
