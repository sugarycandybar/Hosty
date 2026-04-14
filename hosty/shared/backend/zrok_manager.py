"""
Zrok tunnel manager.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import tarfile
from pathlib import Path
from typing import Optional

from hosty.shared.core.events import EventEmitter
from hosty.shared.utils.constants import DATA_DIR


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
# Extracts "zrok access private <token>"
ACCESS_COMMAND_RE = re.compile(r"(zrok access private [A-Za-z0-9_-]+)")
# Or just any share token zrok spits out
SHARE_TOKEN_RE = re.compile(r"access your share with:\s*(zrok access private [A-Za-z0-9_-]+)", re.IGNORECASE)


class ZrokManager(EventEmitter):
    """Manage a single zrok tunnel subprocess."""

    def __init__(self):
        super().__init__()
        self._process: Optional[subprocess.Popen] = None
        self._server_id: Optional[str] = None
        self._status = "stopped"
        self._public_endpoint = ""
        self._share_command = ""

    @property
    def status(self) -> str:
        return self._status

    @property
    def public_endpoint(self) -> str:
        return self._public_endpoint

    @property
    def share_command(self) -> str:
        return self._share_command

    @property
    def server_id(self) -> Optional[str]:
        return self._server_id

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def binary_path(self) -> Path:
        filename = "zrok.exe" if sys.platform == "win32" else "zrok"
        return DATA_DIR / "zrok" / filename

    def resolve_binary(self) -> Optional[str]:
        bundled = self.binary_path
        if bundled.exists():
            return str(bundled)

        system_bin = shutil.which("zrok")
        if system_bin:
            return system_bin

        return None

    def is_installed(self) -> bool:
        return self.resolve_binary() is not None

    def is_running_for(self, server_id: str) -> bool:
        return self.is_running and self._server_id == server_id

    def install_latest_binary(self) -> tuple[bool, str]:
        """Download and install latest zrok binary for this platform."""
        try:
            release_url = "https://api.github.com/repos/openziti/zrok/releases/latest"
            req = urllib.request.Request(
                release_url,
                headers={"User-Agent": "Hosty/1.0", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            assets = data.get("assets") or []
            if not isinstance(assets, list):
                return False, "Release assets unavailable"

            asset = self._select_asset(assets)
            if not asset:
                return False, "No compatible zrok build found for this platform"

            download_url = str(asset.get("browser_download_url", "")).strip()
            if not download_url:
                return False, "Download URL missing"

            target = self.binary_path
            target.parent.mkdir(parents=True, exist_ok=True)

            tar_path = target.parent / "zrok.tar.gz"

            req_bin = urllib.request.Request(download_url, headers={"User-Agent": "Hosty/1.0"})
            with urllib.request.urlopen(req_bin, timeout=120.0) as resp:
                with open(tar_path, "wb") as f:
                    f.write(resp.read())

            if tar_path.name.endswith(".tar.gz"):
                with tarfile.open(tar_path, "r:gz") as tar:
                    tar.extractall(path=target.parent)
                tar_path.unlink()

            if sys.platform != "win32" and target.exists():
                target.chmod(0o755)

            return True, str(target)
        except Exception as e:
            return False, str(e)

    def _select_asset(self, assets: list[dict]) -> Optional[dict]:
        sys_name = platform.system().lower()
        machine = platform.machine().lower()

        if machine in {"x86_64", "amd64"}:
            arch_keys = ("amd64", "x86_64", "x64")
        elif machine in {"aarch64", "arm64"}:
            arch_keys = ("aarch64", "arm64")
        elif machine in {"armv7l", "armv7"}:
            arch_keys = ("armv7",)
        else:
            arch_keys = (machine,)

        if "windows" in sys_name:
            os_keys = ("windows", "win")
        elif "darwin" in sys_name or "mac" in sys_name:
            os_keys = ("mac", "darwin", "osx")
        else:
            os_keys = ("linux",)

        candidates = []
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            if not name:
                continue
            if not name.endswith(".tar.gz") and not name.endswith(".zip"):
                continue
            if not any(k in name for k in os_keys):
                continue
            if not any(k in name for k in arch_keys):
                continue
            candidates.append(asset)

        if candidates:
            return candidates[0]

        return None

    def enable(self, token: str) -> tuple[bool, str]:
        """Run zrok enable <token> to link this machine."""
        binary = self.resolve_binary()
        if not binary:
            return False, "Zrok binary not found"
        try:
            result = subprocess.run(
                [binary, "enable", token],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15,
            )
            output = (result.stdout or "").strip()
            if result.returncode == 0 or "already enabled" in output.lower() or "you are already using" in output.lower():
                return True, output
            return False, output
        except Exception as e:
            return False, str(e)

    def check_enabled(self) -> bool:
        """Check if zrok is currently enabled on this machine."""
        binary = self.resolve_binary()
        if not binary:
            return False
        try:
            result = subprocess.run(
                [binary, "status"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
            )
            output = (result.stdout or "").strip()
            if "Unable to load your local environment" in output:
                return False
            # "Environment: ... [ENABLED]" or similar
            return result.returncode == 0
        except Exception:
            return False

    def start(
        self,
        server_id: str,
        server_dir: str,
        port: int = 25565,
        auto_install: bool = False,
    ) -> tuple[bool, str]:
        if self.is_running:
            if self._server_id == server_id:
                return True, "zrok is already running"
            return False, "zrok is already running for another server"

        binary = self.resolve_binary()
        if not binary:
            if auto_install:
                ok, msg = self.install_latest_binary()
                if not ok:
                    return False, f"zrok install failed: {msg}"
                binary = self.resolve_binary()
            if not binary:
                return False, "zrok binary not found"

        if not self.check_enabled():
            return False, "zrok is not enabled/claimed yet"

        # Start private share
        cmd = [binary, "share", "private", f"127.0.0.1:{port}", "--backend-mode", "tcpTunnel", "--headless"]

        popen_kwargs = {
            "cwd": str(server_dir),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "env": os.environ.copy(),
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._public_endpoint = ""
        self._share_command = ""
        self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._share_command)

        try:
            self._process = subprocess.Popen(cmd, **popen_kwargs)
            self._server_id = server_id
            self._set_status("running")
            threading.Thread(target=self._read_output, daemon=True).start()
            threading.Thread(target=self._watch_exit, daemon=True).start()
            return True, "zrok started"
        except Exception as e:
            self._process = None
            self._server_id = None
            self._set_status("stopped")
            return False, str(e)

    def stop(self) -> tuple[bool, str]:
        if not self.is_running:
            self._server_id = None
            self._public_endpoint = ""
            self._share_command = ""
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._share_command)
            self._set_status("stopped")
            return True, "zrok is not running"

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
            self._public_endpoint = ""
            self._share_command = ""
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._share_command)
            self._set_status("stopped")

        return True, "zrok stopped"

    def _set_status(self, status: str):
        if self._status != status:
            self._status = status
            self.emit_on_main_thread("status-changed", status)

    def _read_output(self):
        p = self._process
        if not p or not p.stdout:
            return

        buffer = ""
        try:
            while True:
                ch = p.stdout.read(1)
                if not ch:
                    if buffer:
                        self._parse_line_for_endpoints(buffer)
                        self.emit_on_main_thread("output-received", buffer)
                    break

                if ch in ("\n", "\r"):
                    if buffer:
                        self._parse_line_for_endpoints(buffer)
                        self.emit_on_main_thread("output-received", buffer)
                        buffer = ""
                    continue

                buffer += ch
                
                if len(buffer) >= 4096:
                    self._parse_line_for_endpoints(buffer)
                    self.emit_on_main_thread("output-received", buffer)
                    buffer = ""
        except Exception:
            pass

    def _parse_line_for_endpoints(self, line: str):
        text = ANSI_ESCAPE_RE.sub("", line).strip()
        if not text:
            return

        match = SHARE_TOKEN_RE.search(text)
        if match:
            self._share_command = match.group(1).strip()
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._share_command)

        match2 = ACCESS_COMMAND_RE.search(text)
        if match2 and not self._share_command:
            self._share_command = match2.group(1).strip()
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._share_command)

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
            self._public_endpoint = ""
            self._share_command = ""
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._share_command)
            self._set_status("stopped")
