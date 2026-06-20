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

import requests

from hosty.shared.core.events import EventEmitter
from hosty.shared.utils.constants import DATA_DIR
from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ENDPOINT_URL_RE = re.compile(r"(?:tcp|udp)://([A-Za-z0-9.-]+:\d{2,5})")
ENDPOINT_HOSTPORT_RE = re.compile(r"(((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|(?:\d{1,3}\.){3}\d{1,3}):\d{2,5})")
SECRET_VALUE_RE = re.compile(r'(?mi)^\s*(?:secret|secret_key|key)\s*=\s*"([^"]+)"\s*$')
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _split_endpoint(endpoint: str) -> tuple[str, int | None]:
    text = str(endpoint or "").strip()
    if not text:
        return "", None
    text = re.sub(r"^[a-z]+://", "", text, flags=re.IGNORECASE)
    if ":" not in text:
        return text, None
    host, port_text = text.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError:
        port = None
    return host.strip(), port


class PlayitManager(EventEmitter):
    """Manage playit tunnels for multiple servers via a single agent process."""

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
        def __init__(self, parent: PlayitManager, tunnel_data: dict):
            self._parent = parent
            self._cost = int(tunnel_data.get("port_count", 1) or 1)

            self.id = str(tunnel_data.get("id", ""))
            self.name = str(tunnel_data.get("name", ""))
            self.type = tunnel_data.get("tunnel_type") or "both"
            self.protocol = tunnel_data.get("port_type") or "tcp"
            self.status = str((tunnel_data.get("alloc") or {}).get("status", "pending"))

            self.region = ""
            self.port: int | None = None
            self.host = ""
            self.domain = ""
            self.remote_port: int | None = None
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

        self._process: subprocess.Popen | None = None
        self._active_server_ids: dict[str, dict] = {}
        self._status = "stopped"
        self._claim_url = ""
        self._read_thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None

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
        self.tcp_limit = 4
        self.udp_limit = 4
        self.tunnels: dict[str, list[PlayitManager.Tunnel]] = {
            "tcp": [],
            "udp": [],
            "both": [],
        }

        self.initialized = False
        self._agent_id: str | None = None
        self._proto_key: str | None = None
        self._secret_key: str | None = None
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
        for info in self._active_server_ids.values():
            if info.get("endpoint"):
                return info["endpoint"]
        return ""

    def get_endpoint_for(self, server_id: str) -> str:
        info = self._active_server_ids.get(server_id)
        return info.get("endpoint", "") if info else ""

    @property
    def claim_url(self) -> str:
        return self._claim_url

    @property
    def server_id(self) -> str | None:
        return next(iter(self._active_server_ids), None)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def binary_path(self) -> Path:
        filename = "playit.exe" if sys.platform == "win32" else "playit"
        return self.directory / filename

    def resolve_binary(self) -> str | None:
        bundled = self.binary_path
        if bundled.exists() and bundled.is_file():
            return str(bundled)

        system_bin = shutil.which("playit")
        if system_bin:
            return system_bin

        return None

    def _is_pinned_binary(self) -> bool:
        marker = self.directory / ".playit-version"
        return marker.exists() and marker.read_text(encoding="utf-8").strip() == "v0.17.1"

    def is_installed(self) -> bool:
        return self.resolve_binary() is not None

    def is_running_for(self, server_id: str) -> bool:
        return self.is_running and server_id in self._active_server_ids

    def get_running_server_ids(self) -> list[str]:
        return list(self._active_server_ids.keys()) if self.is_running else []

    def _set_status(self, status: str):
        if self._status != status:
            self._status = status
            self.emit_on_main_thread("status-changed", status)

    def _emit_endpoint_changed(self, server_id: str = ""):
        self.emit_on_main_thread("endpoint-changed", self.public_endpoint, self._claim_url)

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
            raise RuntimeError(_("Invalid playit API response"))
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

    def secret_path(self) -> Path | None:
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
                **hidden_subprocess_kwargs(),
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
        # Try v1.x style first: playit version
        try:
            result = subprocess.run(
                [binary, "version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=6,
                **hidden_subprocess_kwargs(),
            )
            text = (result.stdout or "").strip()
            match = VERSION_RE.search(text)
            if match:
                version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return version
        except Exception:
            pass

        # Try old style: playit --version
        try:
            result = subprocess.run(
                [binary, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=6,
                **hidden_subprocess_kwargs(),
            )
            text = (result.stdout or "").strip()
            match = VERSION_RE.search(text)
            if match:
                version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return version
        except Exception:
            pass

        return 0, 17, 1

    def _is_incompatible_version(self, binary: str) -> bool:
        """Check if binary version is 1.x (incompatible with subprocess approach)."""
        major, minor, patch = self._detect_version(binary)
        is_incompatible = major >= 1
        return is_incompatible

    def _download_specific_version(self, version_tag: str) -> tuple[bool, str]:
        """Download a specific release version (e.g., 'v0.17.1')."""
        try:
            release_url = f"https://api.github.com/repos/playit-cloud/playit-agent/releases/tags/{version_tag}"
            req = urllib.request.Request(
                release_url,
                headers={"User-Agent": "Hosty/1.0", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            assets = data.get("assets") or []
            if not isinstance(assets, list):
                return False, _("Release assets unavailable")

            asset = self._select_asset(assets)
            if not asset:
                return False, _("No compatible playit build found for this platform")

            download_url = str(asset.get("browser_download_url", "")).strip()
            if not download_url:
                return False, _("Download URL missing")

            target = self.binary_path
            target.parent.mkdir(parents=True, exist_ok=True)

            req_bin = urllib.request.Request(download_url, headers={"User-Agent": "Hosty/1.0"})
            with urllib.request.urlopen(req_bin, timeout=120.0) as resp:
                payload = resp.read()

            with open(target, "wb") as f:
                f.write(payload)

            if sys.platform != "win32":
                target.chmod(0o755)

            marker = target.parent / ".playit-version"
            marker.write_text(version_tag, encoding="utf-8")

            return True, str(target)
        except Exception as e:
            return False, str(e)

    def install_latest_binary(self) -> tuple[bool, str]:
        """Download and install playit binary (pinned to v0.17.1 for compatibility)."""
        return self._download_specific_version("v0.17.1")

    def _select_asset(self, assets: list[dict]) -> dict | None:
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
            return False, _("Missing playit setup code")

        binary = self.resolve_binary()
        if not binary:
            return False, _("playit binary not found")

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
            return False, _("Failed to reach playit link service: {}").format(e)

        raw_text = response.text
        try:
            data = response.json()
        except ValueError:
            return False, _("Link service returned invalid JSON (HTTP {}): {}").format(response.status_code, raw_text)

        if response.status_code >= 400:
            error_detail = data.get("error") or data.get("message") or data.get("detail") or raw_text
            return False, _("Link service returned HTTP {}: {}").format(response.status_code, error_detail)

        if data.get("status", "fail") == "success":
            payload_data = data.get("data") or {}
            self._agent_id = str(payload_data.get("agent_id", "") or "") or None
            self._secret_key = str(payload_data.get("agent_secret_key", "") or "") or None

        if not self._secret_key:
            return False, _("Link service did not return a key: {}").format(data)

        if not self._write_secret_key(self._secret_key):
            return False, _("Failed to write playit.toml")

        self._claim_url = ""
        self._emit_endpoint_changed()
        self.initialized = False

        # Warm up API session, but don't fail linking if playit API is briefly out-of-sync.
        if self._initialize_with_retry(max_attempts=15, delay_seconds=1.0):
            return True, _("playit account linked")

        if self._is_invalid_agent_key_error(self._last_error):
            self.unlink_account()
            return (
                False,
                _("playit rejected the linked key (InvalidAgentKey). Please generate a new setup code and try again"),
            )

        return True, _("playit account linked (API sync pending)")

    def validate_existing_link(self, retry_attempts: int = 3) -> tuple[bool, str]:
        if not self.read_claimed_secret():
            return False, _("not linked")

        if self._initialize_with_retry(max_attempts=max(1, int(retry_attempts)), delay_seconds=0.5):
            return True, _("linked")

        detail = self._last_error or _("unknown error")
        if self._is_invalid_agent_key_error(detail):
            self.unlink_account()
            return False, _("linked key is invalid and was cleared")

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
            self._last_error = _("playit binary not found")
            return False

        secret = self.read_claimed_secret()
        if not secret:
            self.initialized = False
            self._last_error = _("playit secret key not found")
            return False

        self._secret_key = secret
        self.session.headers["Authorization"] = f"agent-key {self._secret_key}"

        try:
            agent_data = self._request("agents/rundata")
            response_data = agent_data.get("data") or {}
            self._agent_id = str(response_data.get("agent_id", "")) or None
            if not self._agent_id:
                self.initialized = False
                self._last_error = _("agents/rundata did not include agent_id")
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

        tcp_alloc = payload.get("tcp_alloc") or {}
        udp_alloc = payload.get("udp_alloc") or {}
        if "allowed" in tcp_alloc:
            self.tcp_limit = max(1, int(tcp_alloc["allowed"]))
        if "allowed" in udp_alloc:
            self.udp_limit = max(1, int(udp_alloc["allowed"]))
        self.max_tunnels = max(1, int(tcp_alloc.get("allowed", self.max_tunnels)))

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

    def _tunnel_exists_for_endpoint(self, endpoint: str) -> bool:
        """Check if any tunnel in the current list matches the given endpoint."""
        ep = endpoint.strip().lower()
        if not ep:
            return False
        for tunnel in self._return_single_list():
            if tunnel.hostname and tunnel.hostname.strip().lower() == ep:
                return True
            if tunnel.domain and tunnel.remote_port:
                candidate = f"{tunnel.domain}:{tunnel.remote_port}"
                if candidate.lower() == ep:
                    return True
        return False

    def _check_tunnel_limit(self) -> bool:
        total = len(self.tunnels["tcp"]) + len(self.tunnels["udp"]) + len(self.tunnels["both"])
        return total < self.max_tunnels

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

    def _create_tunnel(
        self,
        port: int = 25565,
        protocol: str = "tcp",
        label: str = "",
        tunnel_type: str | None = None,
    ) -> Tunnel | None:
        if not (1024 <= port <= 65535):
            port = 25565

        if not self._check_tunnel_limit():
            raise self.TunnelException(
                _("This account cannot create more than {} tunnel(s). You can increase your limit here: {}").format(
                    self.max_tunnels, "https://playit.gg/account/upgrade"
                )
            )

        if tunnel_type is None:
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

    def _update_tunnel_local_port(self, tunnel_id: str, local_port: int) -> bool:
        """Update the local bind port for an existing tunnel."""
        try:
            # We must provide the full origin data for update
            payload = {
                "tunnel_id": tunnel_id,
                "origin": {
                    "type": "agent",
                    "data": {
                        "agent_id": self._agent_id,
                        "local_ip": "127.0.0.1",
                        "local_port": int(local_port),
                    },
                },
            }
            data = self._request("tunnels/update", json=payload)
            return data.get("status") == "success"
        except Exception:
            return False

    def get_tunnel(
        self,
        port: int,
        protocol: str = "tcp",
        ensure: bool = False,
        label: str = "",
        tunnel_type: str | None = None,
    ) -> Tunnel | None:
        self._retrieve_tunnels()

        for tunnel in self.tunnels.get(protocol, []):
            if tunnel.port == int(port) and not tunnel.in_use:
                return tunnel

        for tunnel in self.tunnels.get(protocol, []):
            if not tunnel.in_use:
                if self._update_tunnel_local_port(tunnel.id, port):
                    tunnel.port = int(port)
                    return tunnel

        if not ensure:
            return None

        return self._create_tunnel(port, protocol, label=label, tunnel_type=tunnel_type)

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
        popen_kwargs.update(hidden_subprocess_kwargs())

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
            if server_id in self._active_server_ids:
                return True, _("playit is already running for this server")

            self._active_server_ids[server_id] = {
                "tunnel_id": None,
                "endpoint": "",
                "port": None,
            }

            return True, _("playit agent is running")

        binary = self.resolve_binary()
        if not binary:
            if auto_install:
                ok, msg = self.install_latest_binary()
                if not ok:
                    return False, _("playit install failed: {}").format(msg)
                binary = self.resolve_binary()
            if not binary:
                return False, _("playit binary not found")

        # Ensure binary is pinned to v0.17.1 (marker avoids repeat downloads)
        if binary and not self._is_pinned_binary():
            ok, msg = self._download_specific_version("v0.17.1")
            if ok:
                binary = self.resolve_binary()
            else:
                return False, _("Failed to install playit v0.17.1: {}").format(msg)

        provided_secret = str(secret or "").strip()
        existing_secret = self.read_claimed_secret()
        if provided_secret and not existing_secret:
            if self._write_secret_key(provided_secret):
                existing_secret = provided_secret

        if not existing_secret:
            if not allow_unclaimed:
                return False, _("playit is not linked yet")
            self._claim_url = self.setup_url
            self._emit_endpoint_changed()
            return True, _("playit setup is required")

        if not self.initialized and not self._initialize_with_retry(max_attempts=25, delay_seconds=1.0):
            detail = self._last_error or _("unknown error")
            if self._is_invalid_agent_key_error(detail):
                self.unlink_account()
                if allow_unclaimed:
                    self._claim_url = self.setup_url
                    self._emit_endpoint_changed()
                    return True, _("playit key invalid, setup is required")
                return False, _("linked playit key is invalid; run setup again")
            return False, _("failed to initialize playit API session: {}").format(detail)

        self._active_server_ids[server_id] = {
            "tunnel_id": None,
            "endpoint": "",
            "port": None,
        }

        if not self._start_agent_service(binary):
            self._active_server_ids.pop(server_id, None)
            return False, _("failed to start playit agent")

        self._claim_url = ""
        self._set_status("running")

        self._read_thread = threading.Thread(target=self._read_output, daemon=True)
        self._read_thread.start()

        self._watch_thread = threading.Thread(target=self._watch_exit, daemon=True)
        self._watch_thread.start()

        return True, _("playit started")

    def regenerate_domain(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
    ) -> tuple[bool, str]:
        """Regenerate the tunnel domain by replacing the server tunnel."""
        # If playit is running for another server, we can still regenerate this server's tunnel
        if self.is_running:
            was_running_for_this = server_id in self._active_server_ids
            if was_running_for_this:
                self._active_server_ids.pop(server_id, None)

            # Delete old tunnel and create a new one via API
            port = self._read_server_port(server_dir)
            protocol = "tcp"
            self._retrieve_tunnels()
            candidates = [tunnel for tunnel in list(self.tunnels.get(protocol, [])) if tunnel.port == int(port)]

            deleted_any = False
            for tunnel in candidates:
                if self._delete_tunnel(tunnel):
                    deleted_any = True

            # Create new tunnel
            try:
                tunnel = self.get_tunnel(port, protocol=protocol, ensure=True, label=server_id)
            except Exception as e:
                return False, str(e)

            if not tunnel:
                return False, _("failed to allocate a new playit tunnel")

            tunnel.in_use = True
            self._active_server_ids[server_id] = {
                "tunnel_id": tunnel.id,
                "endpoint": tunnel.hostname or "",
                "port": port,
            }
            if tunnel.hostname:
                self._emit_endpoint_changed(server_id)

            if deleted_any:
                return True, _("playit tunnel domain regenerated")
            return True, _("playit tunnel created for this server")

        return self.start(
            server_id,
            server_dir,
            secret=secret,
            auto_install=auto_install,
            allow_unclaimed=False,
        )

    def _ensure_api_ready(self, secret: str = "", auto_install: bool = False) -> tuple[bool, str]:
        binary = self.resolve_binary()
        if not binary:
            if auto_install:
                ok, msg = self.install_latest_binary()
                if not ok:
                    return False, _("playit install failed: {}").format(msg)
                binary = self.resolve_binary()
            if not binary:
                return False, _("playit binary not found")

        # Ensure binary is pinned to v0.17.1 (marker avoids repeat downloads)
        if binary and not self._is_pinned_binary():
            ok, msg = self._download_specific_version("v0.17.1")
            if ok:
                binary = self.resolve_binary()
            else:
                return False, _("Failed to install playit v0.17.1: {}").format(msg)

        provided_secret = str(secret or "").strip()
        existing_secret = self.read_claimed_secret()
        if provided_secret and not existing_secret:
            if self._write_secret_key(provided_secret):
                existing_secret = provided_secret

        if not existing_secret:
            return False, _("playit is not linked yet")

        if not self.initialized and not self._initialize_with_retry(max_attempts=25, delay_seconds=1.0):
            detail = self._last_error or _("unknown error")
            if self._is_invalid_agent_key_error(detail):
                self.unlink_account()
                return False, _("linked playit key is invalid; run setup again")
            return False, _("failed to initialize playit API session: {}").format(detail)

        return True, ""

    def _resolve_tunnel_port(self, server_dir: str, protocol: str, bedrock_port: int = 19132) -> int:
        if protocol == "tcp":
            return self._read_server_port(server_dir)

        if 1024 <= bedrock_port <= 65535:
            return bedrock_port
        return 19132

    def _list_tunnels_for_port(self, port: int, protocol: str) -> list[Tunnel]:
        self._retrieve_tunnels()
        return [tunnel for tunnel in list(self.tunnels.get(protocol, [])) if tunnel.port == int(port)]

    def _add_tunnel_for_protocol(
        self,
        server_id: str,
        server_dir: str,
        protocol: str,
        secret: str = "",
        auto_install: bool = False,
        bedrock_port: int = 19132,
        voicechat_port: int = 24454,
        tunnel_kind: str = "",
    ) -> tuple[bool, str, str]:
        ok, msg = self._ensure_api_ready(secret=secret, auto_install=auto_install)
        if not ok:
            return False, msg, ""

        tunnel_type_override: str | None = None
        if tunnel_kind == "voicechat":
            port = voicechat_port if 1024 <= voicechat_port <= 65535 else 24454
            tunnel_label = "voicechat"
            display_name = _("Voice Chat")
        elif tunnel_kind == "bedrock":
            port = self._resolve_tunnel_port(server_dir, protocol, bedrock_port=bedrock_port)
            tunnel_label = "bedrock"
            display_name = _("Bedrock")
        else:
            port = self._resolve_tunnel_port(server_dir, protocol, bedrock_port=bedrock_port)
            tunnel_label = server_id if protocol == "tcp" else f"{server_id}-bedrock"
            display_name = _("Java") if protocol == "tcp" else _("Bedrock")

        try:
            tunnel = self.get_tunnel(
                port,
                protocol=protocol,
                ensure=True,
                label=tunnel_label,
                tunnel_type=tunnel_type_override,
            )
        except Exception as e:
            return False, str(e), ""

        if not tunnel:
            return False, _("failed to allocate a {} playit tunnel").format(protocol.upper()), ""

        endpoint = str(tunnel.hostname or "").strip()
        # For bedrock and voicechat tunnels, include the remote port in the endpoint
        if tunnel_kind in ("bedrock", "voicechat"):
            if tunnel.domain and tunnel.remote_port:
                endpoint = f"{tunnel.domain}:{tunnel.remote_port}"
            elif tunnel.domain:
                endpoint = tunnel.domain

        if endpoint:
            tunnel.in_use = True
            if tunnel_kind not in ("bedrock", "voicechat"):
                self._active_server_ids[server_id] = {
                    "tunnel_id": tunnel.id,
                    "endpoint": endpoint,
                    "port": port,
                }
            if tunnel.hostname:
                self._emit_endpoint_changed(server_id)
            return True, _("{} tunnel ready: {}").format(display_name, endpoint), endpoint
        return True, _("{} tunnel created on {} port {}").format(display_name, protocol.upper(), port), ""

    def _regenerate_tunnel_for_protocol(
        self,
        server_id: str,
        server_dir: str,
        protocol: str,
        secret: str = "",
        auto_install: bool = False,
        bedrock_port: int = 19132,
        voicechat_port: int = 24454,
        tunnel_kind: str = "",
    ) -> tuple[bool, str, str]:
        ok, msg = self._ensure_api_ready(secret=secret, auto_install=auto_install)
        if not ok:
            return False, msg, ""

        if tunnel_kind == "voicechat":
            port = voicechat_port if 1024 <= voicechat_port <= 65535 else 24454
            display_name = _("Voice Chat")
        else:
            port = self._resolve_tunnel_port(server_dir, protocol, bedrock_port=bedrock_port)
            display_name = _("Java") if protocol == "tcp" else _("Bedrock")

        candidates = self._list_tunnels_for_port(port, protocol)

        deleted_any = False
        deleted_hostnames: list[str] = []
        deleted_ids: set[str] = set()
        for tunnel in candidates:
            if self._delete_tunnel(tunnel):
                deleted_any = True
                if tunnel.hostname:
                    deleted_hostnames.append(str(tunnel.hostname))
                if tunnel.id:
                    deleted_ids.add(str(tunnel.id))

        # Clear stale _active_server_ids entries for deleted tunnels
        for sid, sinfo in list(self._active_server_ids.items()):
            if sinfo.get("tunnel_id") in deleted_ids:
                self._active_server_ids[sid]["endpoint"] = ""
                self._active_server_ids[sid]["tunnel_id"] = None
                self._emit_endpoint_changed(sid)
            elif sinfo.get("endpoint") in deleted_hostnames:
                self._active_server_ids[sid]["endpoint"] = ""
                self._emit_endpoint_changed(sid)

        ok, msg, endpoint = self._add_tunnel_for_protocol(
            server_id,
            server_dir,
            protocol,
            secret=secret,
            auto_install=auto_install,
            bedrock_port=bedrock_port,
            voicechat_port=voicechat_port,
            tunnel_kind=tunnel_kind,
        )
        if not ok:
            return False, msg, ""

        if deleted_any and endpoint:
            return True, _("{} tunnel domain regenerated: {}").format(display_name, endpoint), endpoint
        if deleted_any:
            return True, _("{} tunnel domain regenerated").format(display_name), endpoint
        return True, msg, endpoint

    def _delete_tunnel_for_protocol(
        self,
        server_dir: str,
        protocol: str,
        secret: str = "",
        auto_install: bool = False,
        bedrock_port: int = 19132,
        voicechat_port: int = 24454,
        tunnel_kind: str = "",
    ) -> tuple[bool, str]:
        ok, msg = self._ensure_api_ready(secret=secret, auto_install=auto_install)
        if not ok:
            return False, msg

        if tunnel_kind == "voicechat":
            port = voicechat_port if 1024 <= voicechat_port <= 65535 else 24454
            display_name = _("Voice Chat")
        else:
            port = self._resolve_tunnel_port(server_dir, protocol, bedrock_port=bedrock_port)
            display_name = _("Java") if protocol == "tcp" else _("Bedrock")

        candidates = self._list_tunnels_for_port(port, protocol)
        if not candidates:
            return False, _("No {} tunnel found").format(display_name.lower())

        deleted_any = False
        deleted_hostnames: list[str] = []
        deleted_ids: set[str] = set()
        for tunnel in candidates:
            if self._delete_tunnel(tunnel):
                deleted_any = True
                if tunnel.hostname:
                    deleted_hostnames.append(str(tunnel.hostname))
                if tunnel.id:
                    deleted_ids.add(str(tunnel.id))

        if not deleted_any:
            return False, _("Failed to delete {} tunnel").format(display_name.lower())

        # Clear matching tunnel info from any server
        for server_id, info in list(self._active_server_ids.items()):
            if info.get("tunnel_id") in deleted_ids:
                self._active_server_ids[server_id]["endpoint"] = ""
                self._active_server_ids[server_id]["tunnel_id"] = None
                self._emit_endpoint_changed(server_id)
            elif info.get("endpoint") in deleted_hostnames:
                self._active_server_ids[server_id]["endpoint"] = ""
                self._emit_endpoint_changed(server_id)

        return True, _("{} tunnel deleted").format(display_name)

    def _delete_tunnels_by_port(self, port: int, protocol: str) -> tuple[bool, str]:
        candidates = self._list_tunnels_for_port(port, protocol)
        if not candidates:
            return True, _("no tunnels to delete")

        deleted_any = False
        deleted_hostnames: list[str] = []
        deleted_ids: set[str] = set()
        for tunnel in candidates:
            if self._delete_tunnel(tunnel):
                deleted_any = True
                if tunnel.hostname:
                    deleted_hostnames.append(str(tunnel.hostname))
                if tunnel.id:
                    deleted_ids.add(str(tunnel.id))

        for sid, sinfo in list(self._active_server_ids.items()):
            if sinfo.get("tunnel_id") in deleted_ids:
                self._active_server_ids[sid]["endpoint"] = ""
                self._active_server_ids[sid]["tunnel_id"] = None
                self._emit_endpoint_changed(sid)
            elif sinfo.get("endpoint") in deleted_hostnames:
                self._active_server_ids[sid]["endpoint"] = ""
                self._emit_endpoint_changed(sid)

        if not deleted_any:
            return False, _("failed to delete tunnels for port {}").format(port)
        return True, _("deleted {} tunnel(s) for port {}").format(len(deleted_ids), port)

    def add_java_tunnel(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
    ) -> tuple[bool, str, str]:
        return self._add_tunnel_for_protocol(
            server_id,
            server_dir,
            "tcp",
            secret=secret,
            auto_install=auto_install,
        )

    def regenerate_java_tunnel(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
    ) -> tuple[bool, str, str]:
        return self._regenerate_tunnel_for_protocol(
            server_id,
            server_dir,
            "tcp",
            secret=secret,
            auto_install=auto_install,
        )

    def delete_java_tunnel(
        self,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
    ) -> tuple[bool, str]:
        return self._delete_tunnel_for_protocol(
            server_dir,
            "tcp",
            secret=secret,
            auto_install=auto_install,
        )

    def add_bedrock_tunnel(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        bedrock_port: int = 19132,
    ) -> tuple[bool, str, str]:
        return self._add_tunnel_for_protocol(
            server_id,
            server_dir,
            "udp",
            secret=secret,
            auto_install=auto_install,
            bedrock_port=bedrock_port,
            tunnel_kind="bedrock",
        )

    def regenerate_bedrock_tunnel(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        bedrock_port: int = 19132,
    ) -> tuple[bool, str, str]:
        return self._regenerate_tunnel_for_protocol(
            server_id,
            server_dir,
            "udp",
            secret=secret,
            auto_install=auto_install,
            bedrock_port=bedrock_port,
            tunnel_kind="bedrock",
        )

    def delete_bedrock_tunnel(
        self,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        bedrock_port: int = 19132,
    ) -> tuple[bool, str]:
        return self._delete_tunnel_for_protocol(
            server_dir,
            "udp",
            secret=secret,
            auto_install=auto_install,
            bedrock_port=bedrock_port,
            tunnel_kind="bedrock",
        )

    def add_voicechat_tunnel(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        voicechat_port: int = 24454,
    ) -> tuple[bool, str, str]:
        return self._add_tunnel_for_protocol(
            server_id,
            server_dir,
            "udp",
            secret=secret,
            auto_install=auto_install,
            voicechat_port=voicechat_port,
            tunnel_kind="voicechat",
        )

    def regenerate_voicechat_tunnel(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        voicechat_port: int = 24454,
    ) -> tuple[bool, str, str]:
        return self._regenerate_tunnel_for_protocol(
            server_id,
            server_dir,
            "udp",
            secret=secret,
            auto_install=auto_install,
            voicechat_port=voicechat_port,
            tunnel_kind="voicechat",
        )

    def delete_voicechat_tunnel(
        self,
        server_dir: str,
        secret: str = "",
        auto_install: bool = False,
        voicechat_port: int = 24454,
    ) -> tuple[bool, str]:
        return self._delete_tunnel_for_protocol(
            server_dir,
            "udp",
            secret=secret,
            auto_install=auto_install,
            voicechat_port=voicechat_port,
            tunnel_kind="voicechat",
        )

    def _replace_geyser_bedrock_block(self, content: str, port: int) -> str:
        lines = content.splitlines()
        out: list[str] = []
        idx = 0
        replaced = False

        while idx < len(lines):
            line = lines[idx]
            if line.strip() != "bedrock:" or line.startswith((" ", "\t")):
                out.append(line)
                idx += 1
                continue

            out.append("bedrock:")
            idx += 1
            block: list[str] = []
            while idx < len(lines):
                child = lines[idx]
                if child.strip() and not child.startswith((" ", "\t")):
                    break
                block.append(child)
                idx += 1

            seen_address = seen_port = seen_clone = False
            for child in block:
                stripped = child.strip()
                if stripped.startswith("address:"):
                    out.append("  address: 0.0.0.0")
                    seen_address = True
                elif stripped.startswith("port:"):
                    out.append(f"  port: {port}")
                    seen_port = True
                elif stripped.startswith("clone-remote-port:"):
                    out.append("  clone-remote-port: false")
                    seen_clone = True
                else:
                    out.append(child)
            if not seen_address:
                out.append("  address: 0.0.0.0")
            if not seen_port:
                out.append(f"  port: {port}")
            if not seen_clone:
                out.append("  clone-remote-port: false")
            replaced = True

        if not replaced:
            if out and out[-1].strip():
                out.append("")
            out.extend(
                [
                    "bedrock:",
                    "  address: 0.0.0.0",
                    f"  port: {port}",
                    "  clone-remote-port: false",
                ]
            )

        return "\n".join(out) + "\n"

    def _replace_geyser_remote_auth_type(self, content: str, auth_type: str = "floodgate") -> str:
        lines = content.splitlines()
        out: list[str] = []
        idx = 0
        replaced = False

        while idx < len(lines):
            line = lines[idx]
            if line.strip() != "remote:" or line.startswith((" ", "\t")):
                out.append(line)
                idx += 1
                continue

            out.append("remote:")
            idx += 1
            block: list[str] = []
            while idx < len(lines):
                child = lines[idx]
                if child.strip() and not child.startswith((" ", "\t")):
                    break
                block.append(child)
                idx += 1

            seen_auth = False
            for child in block:
                stripped = child.strip()
                if stripped.startswith("auth-type:"):
                    out.append(f"  auth-type: {auth_type}")
                    seen_auth = True
                else:
                    out.append(child)
            if not seen_auth:
                out.append(f"  auth-type: {auth_type}")
            replaced = True

        if not replaced:
            if out and out[-1].strip():
                out.append("")
            out.extend(
                [
                    "remote:",
                    f"  auth-type: {auth_type}",
                ]
            )

        return "\n".join(out) + "\n"

    def configure_geyser_mod(self, server_dir: str, bedrock_port: int = 19132) -> bool:
        """Ensure Geyser's Fabric config listens on the Bedrock UDP port."""
        try:
            port = int(bedrock_port)
        except Exception:
            port = 19132
        if port < 1024 or port > 65535:
            port = 19132

        config_dir = Path(server_dir) / "config" / "Geyser-Fabric"
        config_file = config_dir / "config.yml"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            content = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
            config_file.write_text(self._replace_geyser_bedrock_block(content, port), encoding="utf-8")
            return True
        except Exception:
            return False

    def configure_floodgate_mod(self, server_dir: str) -> bool:
        """Ensure Geyser is configured to use Floodgate authentication."""
        config_dir = Path(server_dir) / "config" / "Geyser-Fabric"
        config_file = config_dir / "config.yml"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            content = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
            content = self._replace_geyser_remote_auth_type(content, "floodgate")
            config_file.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    def _write_voicechat_properties(
        self,
        config_file: Path,
        local_port: int,
        domain: str = "",
    ) -> bool:
        try:
            lines = config_file.read_text(encoding="utf-8").splitlines() if config_file.exists() else []
            replacements = {
                "port": f"port={local_port}",
                "bind_address": "bind_address=0.0.0.0",
            }
            if domain:
                replacements["voice_host"] = f"voice_host={domain}"
            seen: set[str] = set()
            out: list[str] = []
            for line in lines:
                stripped = line.strip()
                key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
                if key in replacements and not stripped.startswith("#"):
                    out.append(replacements[key])
                    seen.add(key)
                else:
                    out.append(line)
            for key, value in replacements.items():
                if key not in seen:
                    out.append(value)
            config_file.write_text("\n".join(out) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def configure_voicechat_mod(
        self,
        server_dir: str,
        server_id: str,
        endpoint: str = "",
        voicechat_port: int = 0,
    ) -> bool:
        """Auto-configure Simple Voice Chat mod to use the playit tunnel.

        1) Extract the assigned UDP address and port from the local Playit agent's status/API.
        2) Open config/voicechat/voicechat-server.properties.
        3) Overwrite port, voice_host, and bind_address.
        4) Save the file, clean up any stale .toml sibling.
        """
        domain, remote_port = _split_endpoint(endpoint)
        voice_tunnel = None
        if not domain or not remote_port:
            self._retrieve_tunnels()
            label_prefix = f"hosty-{re.sub(r'[^a-zA-Z0-9-]', '-', server_id.lower())}-voicechat"

            for tunnel in self.tunnels.get("udp", []):
                if tunnel.name.startswith(label_prefix):
                    voice_tunnel = tunnel
                    break

            if voice_tunnel and voice_tunnel.domain and voice_tunnel.remote_port:
                domain = str(voice_tunnel.domain)
                remote_port = int(voice_tunnel.remote_port)
            else:
                domain = ""
                remote_port = 24454

        local_port = voicechat_port if 1024 <= voicechat_port <= 65535 else remote_port

        # Update Playit to forward to the same port locally so the mod can bind to it.
        if voice_tunnel and voice_tunnel.port != local_port:
            if self._update_tunnel_local_port(voice_tunnel.id, local_port):
                voice_tunnel.port = local_port

        config_dir = Path(server_dir) / "config" / "voicechat"
        config_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale .toml file -- modern SVC only reads .properties
        old_toml = config_dir / "voicechat-server.toml"
        if old_toml.exists():
            try:
                old_toml.unlink()
            except Exception:
                pass

        config_file = config_dir / "voicechat-server.properties"
        return self._write_voicechat_properties(config_file, local_port, domain)

    def verify_playit_mod_configs(
        self,
        server_dir: str,
        server_id: str,
        bedrock_endpoint: str = "",
        voicechat_endpoint: str = "",
        bedrock_port: int = 19132,
        voicechat_port: int = 24454,
    ) -> dict[str, bool]:
        """Best-effort background repair for playit-backed mod config files."""
        result = {"geyser": False, "voicechat": False}
        if str(bedrock_endpoint or "").strip():
            result["geyser"] = self.configure_geyser_mod(server_dir, bedrock_port)
            mods_dir = Path(server_dir) / "mods"
            has_floodgate = False
            try:
                has_floodgate = any("floodgate" in jar.stem.lower() for jar in mods_dir.glob("*.jar"))
            except Exception:
                has_floodgate = False
            if has_floodgate:
                self.configure_floodgate_mod(server_dir)
        if str(voicechat_endpoint or "").strip():
            result["voicechat"] = self.configure_voicechat_mod(
                server_dir,
                server_id,
                endpoint=voicechat_endpoint,
                voicechat_port=voicechat_port,
            )
        return result

    def auto_create_tunnel_mods(
        self,
        server_id: str,
        server_dir: str,
        secret: str = "",
        bedrock_port: int = 19132,
        voicechat_port: int = 24454,
    ) -> dict[str, str]:
        """Auto-create bedrock/voicechat tunnels if mods are installed and no tunnel exists yet.

        Also validates existing tunnel endpoints -- if a configured endpoint no longer has
        a matching tunnel on playit's side (e.g. deleted via dashboard), the endpoint is
        cleared and a new tunnel is created.

        Returns dict with "bedrock_endpoint" and "voicechat_endpoint" (empty if not created).
        """
        from hosty.shared.backend.playit_config import load_playit_config, save_playit_config

        result = {"bedrock_endpoint": "", "voicechat_endpoint": ""}
        cfg = load_playit_config(server_dir)
        mods_dir = Path(server_dir) / "mods"
        dirty = False

        # Refresh tunnel list and validate existing endpoints
        self._retrieve_tunnels()

        bedrock_ep = str(cfg.get("bedrock_endpoint", "")).strip()
        if bedrock_ep and not self._tunnel_exists_for_endpoint(bedrock_ep):
            cfg["bedrock_endpoint"] = ""
            bedrock_ep = ""
            dirty = True

        if not bedrock_ep:
            has_geyser = False
            try:
                has_geyser = any("geyser" in jar.stem.lower() for jar in mods_dir.glob("*.jar"))
            except Exception:
                pass
            if has_geyser:
                ok, _msg, endpoint = self.add_bedrock_tunnel(
                    server_id,
                    server_dir,
                    secret=secret,
                    auto_install=True,
                    bedrock_port=bedrock_port,
                )
                if ok and endpoint:
                    cfg["bedrock_endpoint"] = endpoint
                    result["bedrock_endpoint"] = endpoint
                    dirty = True

        vc_ep = str(cfg.get("voicechat_endpoint", "")).strip()
        if vc_ep and not self._tunnel_exists_for_endpoint(vc_ep):
            cfg["voicechat_endpoint"] = ""
            vc_ep = ""
            dirty = True

        if not vc_ep:
            has_vc = False
            try:
                has_vc = any(
                    "voice-chat" in jar.stem.lower() or "simple-voice-chat" in jar.stem.lower()
                    for jar in mods_dir.glob("*.jar")
                )
            except Exception:
                pass
            if has_vc:
                ok, _msg, endpoint = self.add_voicechat_tunnel(
                    server_id,
                    server_dir,
                    secret=secret,
                    auto_install=True,
                    voicechat_port=voicechat_port,
                )
                if ok and endpoint:
                    cfg["voicechat_endpoint"] = endpoint
                    result["voicechat_endpoint"] = endpoint
                    dirty = True

        if dirty:
            save_playit_config(server_dir, cfg)

        return result

    def stop_server(self, server_id: str) -> tuple[bool, str]:
        """Stop playit for a specific server. Keeps agent running for other servers.

        The tunnel is NOT deleted so the same domain is reused on next start.
        """
        if server_id not in self._active_server_ids:
            return True, _("playit is not running for this server")

        self._active_server_ids.pop(server_id)
        self._emit_endpoint_changed(server_id)

        if not self._active_server_ids:
            return self.stop()

        return True, _("playit stopped for this server")

    def stop(self) -> tuple[bool, str]:
        if not self.is_running:
            self._active_server_ids.clear()
            self._clear_active_tunnel_usage()
            self._claim_url = ""
            self._emit_endpoint_changed()
            self._set_status("stopped")
            return True, _("playit is not running")

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
            self._active_server_ids.clear()
            self._clear_active_tunnel_usage()
            self._claim_url = ""
            self._emit_endpoint_changed()
            self._set_status("stopped")

        return True, _("playit stopped")

    def _clear_active_tunnel_usage(self):
        active_tunnel_ids = {info["tunnel_id"] for info in self._active_server_ids.values() if info.get("tunnel_id")}
        if not active_tunnel_ids:
            for tunnel in self._return_single_list():
                tunnel.in_use = False
            return

        for tunnel in self._return_single_list():
            if tunnel.id in active_tunnel_ids:
                tunnel.in_use = False

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

        if not candidates:
            return

        # Try to match parsed endpoints to known tunnels
        self._retrieve_tunnels()
        all_tunnels = self._return_single_list()

        for candidate in candidates:
            candidate_clean = candidate.strip().lower()
            for server_id, info in list(self._active_server_ids.items()):
                current_ep = info.get("endpoint", "").strip().lower()
                if current_ep == candidate_clean:
                    continue
                # Check if this candidate belongs to this server's tunnel
                tunnel_id = info.get("tunnel_id")
                if tunnel_id:
                    for tunnel in all_tunnels:
                        if tunnel.id != tunnel_id or not tunnel.hostname:
                            continue
                        if tunnel.hostname.strip().lower() == candidate_clean:
                            self._active_server_ids[server_id]["endpoint"] = tunnel.hostname.strip()
                            self._emit_endpoint_changed(server_id)
                            break
                # If no tunnel_id match, update the first unmatched server
                if not info.get("endpoint"):
                    self._active_server_ids[server_id]["endpoint"] = candidate
                    self._emit_endpoint_changed(server_id)
                    break

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
                self._active_server_ids.clear()
                self._clear_active_tunnel_usage()
                self._claim_url = ""
                self._emit_endpoint_changed()
                self._set_status("stopped")
