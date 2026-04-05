"""Platform-aware application factory for Hosty frontends."""

from __future__ import annotations

import sys
from typing import Protocol


class HostyApp(Protocol):
    """Common interface for app frontends."""

    def run(self, argv: list[str]) -> int:
        ...


def create_application() -> HostyApp:
    """Create the appropriate frontend for the current platform."""
    if sys.platform == "win32":
        from hosty.ui.windows.application import HostyWindowsApplication

        return HostyWindowsApplication()

    from hosty.application import HostyApplication

    return HostyApplication()
