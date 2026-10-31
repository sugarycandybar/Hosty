"""Application factory for Hosty's GTK frontend."""

from __future__ import annotations

from typing import Protocol

import hosty.i18n  # noqa: F401 - installs _() into builtins early


class HostyApp(Protocol):
    """Common interface for app frontends."""

    def run(self, argv: list[str]) -> int: ...


def create_application() -> HostyApp:
    """Create the GTK frontend."""
    from hosty.gtk_ui.application import HostyApplication

    return HostyApplication()
