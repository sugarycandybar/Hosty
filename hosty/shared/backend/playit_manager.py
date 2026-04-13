"""
Playit tunnel manager.
"""
from __future__ import annotations

import json
import ipaddress
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Optional

from hosty.shared.core.events import EventEmitter
from hosty.shared.utils.constants import DATA_DIR


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ENDPOINT_URL_RE = re.compile(r"(?:tcp|udp)://([A-Za-z0-9.-]+:\d{2,5})")
ENDPOINT_HOSTPORT_RE = re.compile(
    r"(((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|(?:\d{1,3}\.){3}\d{1,3}):\d{2,5})"
)
SECRET_VALUE_RE = re.compile(r'(?mi)^\s*(?:secret|secret_key|key)\s*=\s*"([^"]+)"\s*$')


class PlayitManager(EventEmitter):
    """Manage a single playit tunnel subprocess."""

    def __init__(self):
        super().__init__()
        self._process: Optional[subprocess.Popen] = None
        self._server_id: Optional[str] = None
        self._status = "stopped"
        self._public_endpoint = ""
        self._claim_url = ""

    @property
    def status(self) -> str:
        return self._status

    @property
    def public_endpoint(self) -> str:
        return self._public_endpoint

    @property
    def claim_url(self) -> str:
        return self._claim_url

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

    def secret_path(self) -> Optional[Path]:
        binary = self.resolve_binary()
        if not binary:
            return None

        try:
            result = subprocess.run(
                [binary, "--stdout", "secret-path"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=8,
            )
            output = (result.stdout or "").strip()
            if not output:
                return None
            raw = output.splitlines()[-1].strip()
            if not raw:
                return None
            return Path(raw)
        except Exception:
            return None

    def has_claimed_secret(self) -> bool:
        return bool(self.read_claimed_secret())

    def read_claimed_secret(self) -> str:
        path = self.secret_path()
        if not path or not path.exists() or not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return ""
        if not text:
            return ""

        match = SECRET_VALUE_RE.search(text)
        if match:
            return match.group(1).strip()

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) == 1 and "=" not in lines[0]:
            return lines[0]

        return ""

    def install_latest_binary(self) -> tuple[bool, str]:
        """Download and install latest playit binary for this platform."""
        try:
            release_url = "https://api.github.com/repos/playit-cloud/playit-agent/releases/latest"
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
                return False, "No compatible playit build found for this platform"

            download_url = str(asset.get("browser_download_url", "")).strip()
            if not download_url:
                return False, "Download URL missing"

            target = self.binary_path
            target.parent.mkdir(parents=True, exist_ok=True)

            req_bin = urllib.request.Request(download_url, headers={"User-Agent": "Hosty/1.0"})
            with urllib.request.urlopen(req_bin, timeout=120.0) as resp:
                data = resp.read()

            with open(target, "wb") as f:
                f.write(data)

            if sys.platform != "win32":
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
        else:
            arch_keys = (machine,)

        if "windows" in sys_name:
            os_keys = ("windows", "win")
            required_ext = ".exe"
        elif "darwin" in sys_name or "mac" in sys_name:
            os_keys = ("mac", "darwin", "osx")
            required_ext = ""
        else:
            os_keys = ("linux",)
            required_ext = ""

        candidates = []
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            if not name:
                continue
            if name.endswith(".sha256") or name.endswith(".sig"):
                continue
            if not any(k in name for k in os_keys):
                continue
            if not any(k in name for k in arch_keys):
                continue
            if required_ext and not name.endswith(required_ext):
                continue
            candidates.append(asset)

        if candidates:
            return candidates[0]

        # fallback for generic linux binary names without arch tags
        if "linux" in os_keys:
            for asset in assets:
                name = str(asset.get("name", "")).lower()
                if name.startswith("playit-linux") and not name.endswith(".sha256"):
                    return asset

        return None

    def start(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        allow_unclaimed: bool = False,
    ) -> tuple[bool, str]:
        if self.is_running:
            if self._server_id == server_id:
                return True, "playit is already running"
            return False, "playit is already running for another server"

        binary = self.resolve_binary()
        if not binary:
            if auto_install:
                ok, msg = self.install_latest_binary()
                if not ok:
                    return False, f"playit install failed: {msg}"
                binary = self.resolve_binary()
            if not binary:
                return False, "playit binary not found"

        env = os.environ.copy()

        claimed_secret = self.read_claimed_secret()

        cmd = [binary, "--stdout"]
        if claimed_secret:
            # Prefer the locally claimed secret to avoid stale per-server overrides.
            pass
        elif secret.strip():
            cmd.extend(["--secret", secret.strip()])
        elif not allow_unclaimed:
            return False, "playit is not claimed yet"
        cmd.append("start")

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

        self._public_endpoint = ""
        self._claim_url = ""
        self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)

        try:
            self._process = subprocess.Popen(cmd, **popen_kwargs)
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
            self._public_endpoint = ""
            self._claim_url = ""
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)
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
            self._public_endpoint = ""
            self._claim_url = ""
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)
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

                # Flush very long chunks even if newline is not emitted.
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

        # Capture claim URLs such as https://playit.gg/claim/...
        for url in re.findall(r"https?://\S+", text):
            clean = url.rstrip(".,;)]}")
            if "playit.gg/claim" in clean and clean != self._claim_url:
                self._claim_url = clean
                self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)

        candidates: list[str] = []
        candidates.extend(ENDPOINT_URL_RE.findall(text))
        candidates.extend(ENDPOINT_HOSTPORT_RE.findall(text))

        best = self._pick_best_endpoint(candidates)
        if not best:
            return

        current_score = self._endpoint_score(self._public_endpoint) if self._public_endpoint else -1
        best_score = self._endpoint_score(best)
        if best_score > current_score or (best_score == current_score and best != self._public_endpoint):
            self._public_endpoint = best
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)

    def _pick_best_endpoint(self, candidates: list[str]) -> str:
        best = ""
        best_score = -1
        for endpoint in candidates:
            score = self._endpoint_score(endpoint)
            if score > best_score:
                best = endpoint
                best_score = score
        return best

    def _endpoint_score(self, endpoint: str) -> int:
        if not endpoint or ":" not in endpoint:
            return -1

        host = endpoint.rsplit(":", 1)[0].strip().lower()
        if not host:
            return -1

        if self._is_private_or_loopback_ipv4(host):
            return -1
        if host.endswith("joinmc.link"):
            return 100
        if any(c.isalpha() for c in host):
            return 80
        if self._is_ipv4(host):
            return 40
        return 10

    def _is_ipv4(self, value: str) -> bool:
        try:
            ipaddress.IPv4Address(value)
            return True
        except Exception:
            return False

    def _is_private_or_loopback_ipv4(self, value: str) -> bool:
        try:
            ip = ipaddress.IPv4Address(value)
            return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
        except Exception:
            return False

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
            self._claim_url = ""
            self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)
            self._set_status("stopped")
