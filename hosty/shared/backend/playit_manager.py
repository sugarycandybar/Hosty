"""
Playit tunnel manager.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime as dt
from pathlib import Path
from typing import Optional

import requests

from hosty.shared.core.events import EventEmitter
from hosty.shared.utils.constants import DATA_DIR


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ENDPOINT_URL_RE = re.compile(r"(?:tcp|udp)://([A-Za-z0-9.-]+:\d{2,5})")
ENDPOINT_HOSTPORT_RE = re.compile(
    r"(((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|(?:\d{1,3}\.){3}\d{1,3}):\d{2,5})"
)
SECRET_VALUE_RE = re.compile(r'(?mi)^\s*(?:secret|secret_key|key)\s*=\s*"([^"]+)"\s*$')
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class PlayitManager(EventEmitter):
    """Manage a single playit tunnel subprocess."""

    setup_url = "https://playit.gg/account/setup/wizard/new-account/third-party/third-party-code?partner=hosty"

    class TunnelException(Exception):
        pass

    class TunnelCacheHelper:
        def __init__(self, root_path: Path):
            self._path = Path(root_path) / "tunnel-cache.json"
            self._data: dict[str, dict] = {}
            self._read_data()

        def _read_data(self):
            if self._path.exists() and self._path.is_file():
                try:
                    self._data = json.loads(self._path.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    self._data = {}

        def _write_data(self):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data), encoding="utf-8")

        def clear_cache(self):
            if self._path.exists():
                self._path.unlink(missing_ok=True)
            self._data = {}

        def add_tunnel(self, tunnel_id: str, data: dict) -> bool:
            self._data[str(tunnel_id)] = dict(data)
            self._write_data()
            return str(tunnel_id) in self._data

        def remove_tunnel(self, tunnel_id: str) -> bool:
            tid = str(tunnel_id)
            if tid in self._data:
                del self._data[tid]
            self._write_data()
            return tid not in self._data

        def get_tunnel(self, tunnel_id: str) -> dict:
            return dict(self._data.get(str(tunnel_id), {}))

    class Tunnel:
        def __init__(self, parent: "PlayitManager", tunnel_data: dict):
            self._parent = parent
            self._cost = int(tunnel_data.get("port_count", 1) or 1)

            self.id = str(tunnel_data.get("id", ""))
            self.type = tunnel_data.get("tunnel_type") or "both"
            self.protocol = tunnel_data.get("port_type") or "tcp"
            self.status = str((tunnel_data.get("alloc") or {}).get("status", "pending"))

            self.region = ""
            self.port: Optional[int] = None
            self.host = ""
            self.domain = ""
            self.remote_port: Optional[int] = None
            self.hostname = ""
            self.created = dt.now().astimezone()
            self.in_use = False

            if self.status == "pending":
                return

            alloc = (tunnel_data.get("alloc") or {}).get("data") or {}
            self.region = str(alloc.get("region", ""))

            try:
                origin = (tunnel_data.get("origin") or {}).get("data") or {}
                self.port = int(origin.get("local_port"))
                self.host = str(origin.get("local_ip", ""))
            except Exception:
                cached = self._parent.tunnel_cache.get_tunnel(self.id)
                cached_origin = (cached.get("origin") or {}).get("data") or {}
                try:
                    self.port = int(cached_origin.get("local_port"))
                    self.host = str(cached_origin.get("local_ip", ""))
                except Exception:
                    self.port = None
                    self.host = ""

            self.domain = str(alloc.get("assigned_domain", ""))
            try:
                self.remote_port = int(alloc.get("port_start"))
            except Exception:
                self.remote_port = None

            if self.type == "both" and self.remote_port:
                self.hostname = f"{self.domain}:{self.remote_port}"
            else:
                self.hostname = self.domain

            raw_date = str(tunnel_data.get("created_at", "")).strip()
            if raw_date:
                try:
                    date_obj = dt.fromisoformat(raw_date.replace("Z", "+00:00"))
                    self.created = date_obj.astimezone(dt.now().astimezone().tzinfo)
                except Exception:
                    pass

        def __repr__(self):
            return f"<PlayitManager.Tunnel '{self.hostname}'>"

        def delete(self):
            self._parent._delete_tunnel(self)

    def __init__(self):
        super().__init__()

        self._process: Optional[subprocess.Popen] = None
        self._server_id: Optional[str] = None
        self._status = "stopped"
        self._public_endpoint = ""
        self._claim_url = ""
        self._read_thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None

        self._git_base = "https://github.com/playit-cloud/playit-agent/releases"
        self._api_base = "https://api.playit.gg"
        self._web_base = "https://playit.gg"
        self._link_worker_url = "https://playit.auto-mcs.com/link"

        self.provider = "playit"
        self.directory = DATA_DIR / "playit"
        self.toml_path = self.directory / "playit.toml"
        self.tunnel_cache = self.TunnelCacheHelper(self.directory)
        self.config: dict[str, str] = {}

        self.session = requests.Session()
        self.agent_name = f"hosty ({platform.node()})"
        self.agent_web_url = ""
        self.max_tunnels = 4
        self.tunnels: dict[str, list[PlayitManager.Tunnel]] = {
            "tcp": [],
            "udp": [],
            "both": [],
        }

        self.initialized = False
        self._agent_id: Optional[str] = None
        self._proto_key: Optional[str] = None
        self._secret_key: Optional[str] = None
        self._active_tunnel_id: Optional[str] = None
        self._last_error = ""

    def _is_invalid_agent_key_error(self, detail: str) -> bool:
        text = str(detail or "")
        lowered = text.lower()
        return "invalidagentkey" in lowered or ("401" in lowered and "auth" in lowered)

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
        return self.directory / filename

    def resolve_binary(self) -> Optional[str]:
        bundled = self.binary_path
        if bundled.exists() and bundled.is_file():
            return str(bundled)

        system_bin = shutil.which("playit")
        if system_bin:
            return system_bin

        return None

    def is_installed(self) -> bool:
        return self.resolve_binary() is not None

    def is_running_for(self, server_id: str) -> bool:
        return self.is_running and self._server_id == server_id

    def _set_status(self, status: str):
        if self._status != status:
            self._status = status
            self.emit_on_main_thread("status-changed", status)

    def _emit_endpoint_changed(self):
        self.emit_on_main_thread("endpoint-changed", self._public_endpoint, self._claim_url)

    def _request(self, endpoint: str, **kwargs) -> dict:
        url = f"{self._api_base}/{endpoint.strip('/')}"
        try:
            response = self.session.post(url, timeout=20, **kwargs)
            response.raise_for_status()
        except requests.RequestException as e:
            message = str(e)
            if getattr(e, "response", None) is not None:
                try:
                    status = e.response.status_code
                    body = (e.response.text or "").strip()
                    if len(body) > 240:
                        body = body[:240] + "..."
                    message = f"HTTP {status}: {body}" if body else f"HTTP {status}"
                except Exception:
                    pass
            raise RuntimeError(message) from e

        try:
            payload = response.json()
        except ValueError as e:
            body = (response.text or "").strip()
            if len(body) > 240:
                body = body[:240] + "..."
            raise RuntimeError(f"Invalid JSON response: {body}") from e

        if not isinstance(payload, dict):
            raise RuntimeError("Invalid playit API response")
        return payload

    def _load_config(self) -> bool:
        if not self.toml_path.exists():
            return False

        data: dict[str, str] = {}
        try:
            text = self.toml_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False

        for line in text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().strip("'\"")
            value = value.strip().strip("'\"")
            if key:
                data[key] = value

        self.config = data
        return bool(self.config)

    def _write_secret_key(self, secret_key: str) -> bool:
        key = str(secret_key or "").strip()
        if not key:
            return False

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.toml_path.write_text(f'secret_key = "{key}"\n', encoding="utf-8")
            self.config = {"secret_key": key}
            self._secret_key = key
            return True
        except Exception:
            return False

    def _reset_config(self) -> bool:
        try:
            if self.toml_path.exists():
                self.toml_path.unlink(missing_ok=True)
            self.config = {}
            self._secret_key = None
            return True
        except Exception:
            return False

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
            return Path(raw) if raw else None
        except Exception:
            return None

    def read_claimed_secret(self) -> str:
        if self._load_config():
            secret = str(self.config.get("secret_key", "")).strip()
            if secret:
                return secret

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

    def has_claimed_secret(self) -> bool:
        return bool(self.read_claimed_secret())

    def _detect_version(self, binary: str) -> tuple[int, int, int]:
        try:
            result = subprocess.run(
                [binary, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=6,
            )
            text = (result.stdout or "").strip()
            match = VERSION_RE.search(text)
            if match:
                return int(match.group(1)), int(match.group(2)), int(match.group(3))
        except Exception:
            pass
        return 0, 17, 1

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
                payload = resp.read()

            with open(target, "wb") as f:
                f.write(payload)

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

        if "linux" in os_keys:
            for asset in assets:
                name = str(asset.get("name", "")).lower()
                if name.startswith("playit-linux") and not name.endswith(".sha256"):
                    return asset

        return None

    def _proto_register(self) -> bool:
        binary = self.resolve_binary()
        if not binary:
            return False

        major, minor, patch = self._detect_version(binary)
        platform_name = "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")

        proto_data = {
            "agent_version": {
                "official": True,
                "details_website": None,
                "version": {
                    "platform": platform_name,
                    "version": f"{major}.{minor}.{patch}",
                },
            },
            "client_addr": "0.0.0.0:0",
            "tunnel_addr": "0.0.0.0:0",
        }

        try:
            response = self._request("proto/register", json=proto_data)
        except Exception:
            return False

        if response.get("status") == "success":
            self._proto_key = str((response.get("data") or {}).get("key", "")) or None

        return bool(self._proto_key)

    def link_account(self, setup_code: str, timeout: int = 20) -> tuple[bool, str]:
        code = str(setup_code or "").strip()
        if not code:
            return False, "Missing playit setup code"

        binary = self.resolve_binary()
        if not binary:
            return False, "playit binary not found"

        major, minor, patch = self._detect_version(binary)
        platform_name = "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")

        payload = {
            "account_setup_code": code,
            "agent_name": self.agent_name,
            "platform": platform_name,
            "version_major": major,
            "version_minor": minor,
            "version_patch": patch,
        }

        try:
            response = requests.post(
                self._link_worker_url,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as e:
            return False, f"Failed to reach playit link service: {e}"

        raw_text = response.text
        try:
            data = response.json()
        except ValueError:
            return False, f"Link service returned invalid JSON (HTTP {response.status_code}): {raw_text}"

        if response.status_code >= 400:
            error_detail = (
                data.get("error")
                or data.get("message")
                or data.get("detail")
                or raw_text
            )
            return False, f"Link service returned HTTP {response.status_code}: {error_detail}"

        if data.get("status", "fail") == "success":
            payload_data = data.get("data") or {}
            self._agent_id = str(payload_data.get("agent_id", "") or "") or None
            self._secret_key = str(payload_data.get("agent_secret_key", "") or "") or None

        if not self._secret_key:
            return False, f"Link service did not return a key: {data}"

        if not self._write_secret_key(self._secret_key):
            return False, "Failed to write playit.toml"

        self._claim_url = ""
        self._emit_endpoint_changed()
        self.initialized = False

        # Warm up API session, but don't fail linking if playit API is briefly out-of-sync.
        if self._initialize_with_retry(max_attempts=15, delay_seconds=1.0):
            return True, "playit account linked"

        if self._is_invalid_agent_key_error(self._last_error):
            self.unlink_account()
            return False, "playit rejected the linked key (InvalidAgentKey). Please generate a new setup code and try again"

        return True, "playit account linked (API sync pending)"

    def validate_existing_link(self, retry_attempts: int = 3) -> tuple[bool, str]:
        if not self.read_claimed_secret():
            return False, "not linked"

        if self._initialize_with_retry(max_attempts=max(1, int(retry_attempts)), delay_seconds=0.5):
            return True, "linked"

        detail = self._last_error or "unknown error"
        if self._is_invalid_agent_key_error(detail):
            self.unlink_account()
            return False, "linked key is invalid and was cleared"

        return False, detail

    def unlink_account(self) -> bool:
        reset_ok = self._reset_config()
        self._agent_id = None
        self._proto_key = None
        self._secret_key = None
        self.initialized = False
        self.tunnels = {"tcp": [], "udp": [], "both": []}
        self.tunnel_cache.clear_cache()
        return reset_ok

    def initialize(self) -> bool:
        self._last_error = ""
        binary = self.resolve_binary()
        if not binary:
            self._last_error = "playit binary not found"
            return False

        secret = self.read_claimed_secret()
        if not secret:
            self.initialized = False
            self._last_error = "playit secret key not found"
            return False

        self._secret_key = secret
        self.session.headers["Authorization"] = f"agent-key {self._secret_key}"

        try:
            agent_data = self._request("agents/rundata")
            self._agent_id = str((agent_data.get("data") or {}).get("agent_id", "")) or None
            if not self._agent_id:
                self.initialized = False
                self._last_error = "agents/rundata did not include agent_id"
                return False

            self.agent_web_url = f"{self._web_base}/account/agents/{self._agent_id}"
            self._proto_register()
            self._retrieve_tunnels()
            self.initialized = True
            self._last_error = ""
            return True
        except Exception as e:
            self.initialized = False
            self._last_error = str(e)
            return False

    def _initialize_with_retry(self, max_attempts: int = 10, delay_seconds: float = 1.0) -> bool:
        for attempt in range(max(1, int(max_attempts))):
            if self.initialize():
                return True
            if attempt < max_attempts - 1:
                time.sleep(max(0.0, float(delay_seconds)))
        return False

    def _retrieve_tunnels(self) -> dict[str, list[Tunnel]]:
        self.tunnels = {"tcp": [], "udp": [], "both": []}
        if not self._agent_id:
            return self.tunnels

        try:
            data = self._request("tunnels/list", json={"agent_id": self._agent_id})
        except Exception:
            return self.tunnels

        if data.get("status") != "success":
            return self.tunnels

        payload = data.get("data") or {}
        tunnel_items = payload.get("tunnels") or []
        if not isinstance(tunnel_items, list):
            return self.tunnels

        for tunnel_data in tunnel_items:
            try:
                tunnel = self.Tunnel(self, tunnel_data)
            except Exception:
                continue
            key = tunnel.protocol if tunnel.protocol in self.tunnels else "tcp"
            self.tunnels[key].append(tunnel)

        return self.tunnels

    def _return_single_list(self) -> list[Tunnel]:
        out: list[PlayitManager.Tunnel] = []
        out.extend(self.tunnels["tcp"])
        out.extend(self.tunnels["udp"])
        out.extend(self.tunnels["both"])
        return out

    def _check_tunnel_limit(self) -> bool:
        tunnel_count = sum(t._cost for t in self.tunnels["both"])
        tunnel_count += sum(t._cost for t in self.tunnels["tcp"])
        tunnel_count += sum(t._cost for t in self.tunnels["udp"])
        return tunnel_count < self.max_tunnels

    def _read_server_port(self, server_dir: str) -> int:
        default_port = 25565
        try:
            prop_path = Path(server_dir) / "server.properties"
            if not prop_path.exists() or not prop_path.is_file():
                return default_port
            text = prop_path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("server-port="):
                    value = line.split("=", 1)[1].strip()
                    parsed = int(value)
                    if 1024 <= parsed <= 65535:
                        return parsed
                    return default_port
        except Exception:
            return default_port
        return default_port

    def _create_tunnel(self, port: int = 25565, protocol: str = "tcp", label: str = "") -> Tunnel | None:
        if port not in range(1024, 65535):
            port = 25565

        if not self._check_tunnel_limit():
            raise self.TunnelException(f"This account cannot create more than {self.max_tunnels} tunnel(s)")

        tunnel_type = {
            "tcp": "minecraft-java",
            "udp": "minecraft-bedrock",
            "both": None,
        }.get(protocol, "minecraft-java")

        safe_label = re.sub(r"[^a-zA-Z0-9-]", "-", str(label or "").strip().lower())
        safe_label = re.sub(r"-+", "-", safe_label).strip("-")
        if safe_label and re.fullmatch(r"[0-9a-f-]{32,40}", safe_label):
            safe_label = "server"
        if not safe_label:
            safe_label = "server"
        safe_label = safe_label[:24]
        tunnel_name = f"hosty-{safe_label}-{protocol}-{port}-{int(time.time()) % 100000}"

        tunnel_data = {
            "name": tunnel_name,
            "tunnel_type": tunnel_type,
            "port_type": protocol,
            "port_count": 2 if protocol == "both" else 1,
            "enabled": True,
            "origin": {
                "type": "agent",
                "data": {
                    "agent_id": self._agent_id,
                    "local_ip": "127.0.0.1",
                    "local_port": port,
                },
            },
        }

        try:
            data = self._request("tunnels/create", json=tunnel_data)
            tunnel_id = str((data.get("data") or {}).get("id", ""))
            if not tunnel_id:
                return None

            self.tunnel_cache.add_tunnel(tunnel_id, tunnel_data)

            for _ in range(15):
                self._retrieve_tunnels()
                for tunnel in self.tunnels.get(protocol, []):
                    if tunnel.status != "pending" and tunnel.id == tunnel_id:
                        return tunnel
                time.sleep(1)
        except Exception:
            return None

        return None

    def _delete_tunnel(self, tunnel: Tunnel) -> bool:
        try:
            tunnel_status = self._request("tunnels/delete", json={"tunnel_id": tunnel.id})
        except Exception:
            return False

        if tunnel_status.get("status") != "success":
            return False

        self.tunnel_cache.remove_tunnel(tunnel.id)
        bucket = self.tunnels.get(tunnel.protocol, [])
        if tunnel in bucket:
            bucket.remove(tunnel)

        return tunnel not in self.tunnels.get(tunnel.protocol, [])

    def get_tunnel(self, port: int, protocol: str = "tcp", ensure: bool = False, label: str = "") -> Tunnel | None:
        self._retrieve_tunnels()

        for tunnel in self.tunnels.get(protocol, []):
            if tunnel.port == int(port) and not tunnel.in_use:
                return tunnel

        if not ensure:
            return None

        if not self._check_tunnel_limit():
            all_tunnels = sorted(self._return_single_list(), key=lambda t: t.created)
            for tunnel in all_tunnels:
                tunnel.delete()
                if self._check_tunnel_limit():
                    break

        return self._create_tunnel(port, protocol, label=label)

    def _start_agent_service(self, binary: str) -> bool:
        if self.is_running:
            return True

        cmd = [binary, "-s", "--secret_path", str(self.toml_path)]
        popen_kwargs = {
            "cwd": str(self.directory),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "env": os.environ.copy(),
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(cmd, **popen_kwargs)
            return self._process.poll() is None
        except Exception:
            self._process = None
            return False

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

        provided_secret = str(secret or "").strip()
        existing_secret = self.read_claimed_secret()
        if provided_secret and not existing_secret:
            if self._write_secret_key(provided_secret):
                existing_secret = provided_secret

        if not existing_secret:
            if not allow_unclaimed:
                return False, "playit is not linked yet"
            self._claim_url = self.setup_url
            self._emit_endpoint_changed()
            return True, "playit setup is required"

        if not self.initialized and not self._initialize_with_retry(max_attempts=25, delay_seconds=1.0):
            detail = self._last_error or "unknown error"
            if self._is_invalid_agent_key_error(detail):
                self.unlink_account()
                if allow_unclaimed:
                    self._claim_url = self.setup_url
                    self._emit_endpoint_changed()
                    return True, "playit key invalid, setup is required"
                return False, "linked playit key is invalid; run setup again"
            return False, f"failed to initialize playit API session: {detail}"

        port = self._read_server_port(server_dir)
        protocol = "tcp"

        try:
            tunnel = self.get_tunnel(port, protocol=protocol, ensure=True, label=server_id)
        except Exception as e:
            return False, str(e)

        if not tunnel:
            return False, "failed to allocate a playit tunnel"

        tunnel.in_use = True
        self._active_tunnel_id = tunnel.id
        if tunnel.hostname:
            self._public_endpoint = tunnel.hostname
            self._emit_endpoint_changed()

        if not self._start_agent_service(binary):
            tunnel.in_use = False
            self._active_tunnel_id = None
            return False, "failed to start playit agent"

        self._server_id = server_id
        self._claim_url = ""
        self._set_status("running")

        self._read_thread = threading.Thread(target=self._read_output, daemon=True)
        self._read_thread.start()

        self._watch_thread = threading.Thread(target=self._watch_exit, daemon=True)
        self._watch_thread.start()

        return True, "playit started"

    def regenerate_domain(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
    ) -> tuple[bool, str]:
        """Regenerate the tunnel domain by replacing the server tunnel and restarting playit."""
        if self.is_running and self._server_id != server_id:
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

        provided_secret = str(secret or "").strip()
        existing_secret = self.read_claimed_secret()
        if provided_secret and not existing_secret:
            if self._write_secret_key(provided_secret):
                existing_secret = provided_secret

        if not existing_secret:
            return False, "playit is not linked yet"

        if not self.initialized and not self._initialize_with_retry(max_attempts=25, delay_seconds=1.0):
            detail = self._last_error or "unknown error"
            if self._is_invalid_agent_key_error(detail):
                self.unlink_account()
                return False, "linked playit key is invalid; run setup again"
            return False, f"failed to initialize playit API session: {detail}"

        port = self._read_server_port(server_dir)
        protocol = "tcp"

        if self.is_running_for(server_id):
            self.stop()

        self._retrieve_tunnels()
        candidates = [
            tunnel
            for tunnel in list(self.tunnels.get(protocol, []))
            if tunnel.port == int(port)
        ]

        deleted_any = False
        for tunnel in candidates:
            if self._delete_tunnel(tunnel):
                deleted_any = True

        ok, msg = self.start(
            server_id,
            server_dir,
            secret=provided_secret or existing_secret,
            auto_install=auto_install,
            allow_unclaimed=False,
        )
        if not ok:
            return False, msg

        if deleted_any:
            return True, "playit tunnel domain regenerated"
        return True, "playit tunnel restarted"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running:
            self._server_id = None
            self._clear_active_tunnel_usage()
            self._public_endpoint = ""
            self._claim_url = ""
            self._emit_endpoint_changed()
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
            self._clear_active_tunnel_usage()
            self._public_endpoint = ""
            self._claim_url = ""
            self._emit_endpoint_changed()
            self._set_status("stopped")

        return True, "playit stopped"

    def _clear_active_tunnel_usage(self):
        if not self._active_tunnel_id:
            return

        for tunnel in self._return_single_list():
            if tunnel.id == self._active_tunnel_id:
                tunnel.in_use = False
                break

        self._active_tunnel_id = None

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

        for url in re.findall(r"https?://\S+", text):
            clean = url.rstrip(".,;)]}")
            if "playit.gg/claim" in clean and clean != self._claim_url:
                self._claim_url = clean
                self._emit_endpoint_changed()

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
            self._emit_endpoint_changed()

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
            if self._process is p:
                self._process = None
                self._server_id = None
                self._clear_active_tunnel_usage()
                self._public_endpoint = ""
                self._claim_url = ""
                self._emit_endpoint_changed()
                self._set_status("stopped")
