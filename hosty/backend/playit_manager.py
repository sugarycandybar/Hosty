"""
Playit tunnel manager.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from hosty.core.events import EventEmitter
from hosty.utils.constants import DATA_DIR


class PlayitManager(EventEmitter):
    """Manage a single playit tunnel subprocess."""

    def __init__(self):
        super().__init__()
        self._process: Optional[subprocess.Popen] = None
        self._server_id: Optional[str] = None
        self._status = "stopped"

    @property
    def status(self) -> str:
        return self._status

    @property
    def server_id(self) -> Optional[str]:
        return self._server_id

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def binary_path(self) -> Path:
        filename = "playit.exe" if sys.platform == "win32" else "playit"
        return DATA_DIR / "playit" / filename

    def resolve_binary(self) -> Optional[str]:
        bundled = self.binary_path
        if bundled.exists():
            return str(bundled)

        system_bin = shutil.which("playit")
        if system_bin:
            return system_bin

        return None

    def is_installed(self) -> bool:
        return self.resolve_binary() is not None

    def is_running_for(self, server_id: str) -> bool:
        return self.is_running and self._server_id == server_id

    def start(self, server_id: str, server_dir: str, secret: str = "") -> tuple[bool, str]:
        if self.is_running:
            if self._server_id == server_id:
                return True, "playit is already running"
            return False, "playit is already running for another server"

        binary = self.resolve_binary()
        if not binary:
            return False, "playit binary not found"

        env = os.environ.copy()
        if secret.strip():
            # playit supports secret/token based auth in env for headless sessions.
            env["PLAYIT_SECRET"] = secret.strip()

        popen_kwargs = {
            "cwd": str(server_dir),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "env": env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen([binary], **popen_kwargs)
            self._server_id = server_id
            self._set_status("running")
            threading.Thread(target=self._read_output, daemon=True).start()
            threading.Thread(target=self._watch_exit, daemon=True).start()
            return True, "playit started"
        except Exception as e:
            self._process = None
            self._server_id = None
            self._set_status("stopped")
            return False, str(e)

    def stop(self) -> tuple[bool, str]:
        if not self.is_running:
            self._server_id = None
            self._set_status("stopped")
            return True, "playit is not running"

        try:
            assert self._process is not None
            self._process.terminate()
            self._process.wait(timeout=4)
        except Exception:
            try:
                assert self._process is not None
                self._process.kill()
            except Exception:
                pass
        finally:
            self._process = None
            self._server_id = None
            self._set_status("stopped")

        return True, "playit stopped"

    def _set_status(self, status: str):
        if self._status != status:
            self._status = status
            self.emit_on_main_thread("status-changed", status)

    def _read_output(self):
        p = self._process
        if not p or not p.stdout:
            return
        try:
            for line in iter(p.stdout.readline, ""):
                if not line:
                    break
                self.emit_on_main_thread("output-received", line)
        except Exception:
            pass

    def _watch_exit(self):
        p = self._process
        if not p:
            return
        try:
            p.wait()
        except Exception:
            pass
        finally:
            self._process = None
            self._server_id = None
            self._set_status("stopped")
