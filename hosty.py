#!/usr/bin/env python3
"""
Hosty - Fabric Minecraft Server Manager
A modern libadwaita application for creating, running,
and managing Fabric Minecraft servers.
"""
import sys

from hosty.factory import create_application


def main():
    """Launch the Hosty application."""
    try:
        app = create_application()
        return app.run(sys.argv)
    except NotImplementedError as exc:
        print(f"Hosty startup error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Hosty startup error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())