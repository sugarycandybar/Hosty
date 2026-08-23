"""In-process hosting of the management HTTP server from the desktop app.

Lets the normal GUI serve the management web UI (and API) so users don't
need a separate daemon process: enable the toggle in preferences and the
app serves it while running, including in background/autostart mode.
"""

from __future__ import annotations

import json
import threading

from hosty.daemon.server import HostyDaemonServer
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.utils.constants import DATA_DIR
from hosty.shared.utils.file_utils import atomic_write_json

DAEMON_CONFIG_FILE = DATA_DIR / "daemon.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 25570


def load_daemon_config() -> dict[str, str]:
    """Read host/port/token from daemon.json, falling back to defaults."""
    config = {"host": DEFAULT_HOST, "port": str(DEFAULT_PORT), "token": ""}
    try:
        with open(DAEMON_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in ("host", "port", "token"):
                value = data.get(key)
                if value is not None:
                    config[key] = str(value)
    except (OSError, ValueError):
        pass
    return config


def save_daemon_config(host: str, port: int, token: str) -> None:
    """Persist daemon settings so a bare `--headless` run uses the same config."""
    try:
        atomic_write_json(
            DAEMON_CONFIG_FILE,
            {"host": host, "port": int(port), "token": token},
        )
    except OSError:
        pass


class DaemonHost:
    """Owns the lifecycle of the in-process HTTP server."""

    def __init__(self) -> None:
        self._server: HostyDaemonServer | None = None
        self._thread: threading.Thread | None = None
        self._host = DEFAULT_HOST
        self._port = DEFAULT_PORT
        self._token = ""

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(self, manager: ServerManager, host: str, port: int, token: str) -> tuple[bool, str | None]:
        """Start the HTTP server using the app's ServerManager. Returns (ok, error)."""
        self.stop()

        if not token:
            return False, "An access token is required."
        if not 1024 <= port <= 65535:
            return False, "Port must be between 1024 and 65535."

        try:
            server = HostyDaemonServer((host, port), token, manager)
        except OSError as exc:
            return False, str(exc)

        self._server = server
        self._host = host
        self._port = port
        self._token = token
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="hosty-daemon")
        self._thread.start()
        save_daemon_config(host, port, token)
        return True, None

    def stop(self) -> None:
        """Stop the HTTP server. Safe to call from any thread but the serving one."""
        server = self._server
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
