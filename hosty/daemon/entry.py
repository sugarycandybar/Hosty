"""Shared entry-point helpers for the headless daemon.

Used by both the source entry point (hosty.py) and the installed
flatpak/system wrapper (bin/hosty.in) so daemon behaviour is identical.
"""

from __future__ import annotations

import sys

from hosty.daemon.host import load_daemon_config
from hosty.daemon.server import run_daemon
from hosty.shared.backend.server_manager import ServerManager


def parse_daemon_args(argv: list[str]) -> tuple[bool, dict[str, str], list[str]]:
    """Extract daemon flags from argv, returning (headless, opts, remaining args)."""
    headless = False
    opts = {"host": "", "port": "", "token": ""}
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--headless":
            headless = True
        elif arg in ("--host", "--port", "--token") and i + 1 < len(argv):
            i += 1
            opts[arg[2:]] = argv[i]
        else:
            rest.append(arg)
        i += 1
    return headless, opts, rest


def run_headless(argv: list[str]) -> int:
    """Launch the headless daemon. Returns a process exit code."""
    _, opts, _ = parse_daemon_args(argv)
    config = load_daemon_config()

    host = opts["host"] or config.get("host", "127.0.0.1")
    port_text = opts["port"] or config.get("port", "25570")
    token = opts["token"] or config.get("token", "")

    try:
        port = int(port_text)
    except ValueError:
        print(f"Invalid port: {port_text}", file=sys.stderr)
        return 2

    manager = ServerManager()
    started = manager.autostart_servers()
    if started:
        print(f"Hosty daemon: autostarted servers: {', '.join(started)}")

    return run_daemon(manager, host=host, port=port, token=token)


if __name__ == "__main__":
    sys.exit(run_headless(sys.argv))
