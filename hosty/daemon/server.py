"""Headless HTTP daemon that exposes Hosty's backend management over HTTP.

Provides a JSON/SSE API (authenticated with a shared token) plus the
bundled management web UI. Built entirely on the Python standard library.
"""

from __future__ import annotations

import hmac
import json
import logging
import queue
import secrets
import signal
import socket
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from hosty.daemon.web import resolve_static
from hosty.shared.backend.server_manager import ServerManager

logger = logging.getLogger(__name__)

COOKIE_NAME = "hosty_token"
KEEPALIVE_SECONDS = 15.0


def run_daemon(
    manager: ServerManager,
    host: str = "127.0.0.1",
    port: int = 25570,
    token: str = "",
) -> int:
    """Run the daemon until interrupted. Returns a process exit code."""
    if not token:
        token = secrets.token_urlsafe(24)
        print(f"Hosty daemon: generated access token: {token}")

    print(f"Hosty daemon: listening on http://{host}:{port}")
    print("Hosty daemon: all /api requests require the access token (web UI logs in with it).")

    server = HostyDaemonServer((host, port), token, manager)

    stop_event = threading.Event()

    def _request_shutdown(_signum: int, _frame: Any) -> None:
        stop_event.set()

    def _wait_for_shutdown() -> None:
        stop_event.wait()
        # server.shutdown() must run on a thread other than the one inside
        # serve_forever(), otherwise it deadlocks.
        server.shutdown()

    try:
        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)
    except (ValueError, OSError):
        pass

    threading.Thread(target=_wait_for_shutdown, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


class HostyDaemonServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], token: str, manager: ServerManager) -> None:
        self.token = token
        self.manager = manager
        super().__init__(server_address, HostyRequestHandler)

    def get_request(self):
        """Accept a connection and disable Nagle so SSE bytes are never stuck."""
        sock, addr = super().get_request()
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return sock, addr


class HostyRequestHandler(BaseHTTPRequestHandler):
    server: HostyDaemonServer

    # HTTP/1.0 lets each response end at connection close unless a
    # Content-Length is supplied, which is exactly the behaviour the
    # Server-Sent Events endpoint needs.
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    # -- dispatch ---------------------------------------------------------

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.error("Unhandled error handling request:\n%s", traceback.format_exc())
            try:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"})
            except Exception:
                pass

    def _route(self) -> None:
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        parts = [part for part in path.split("/") if part]

        if not path.startswith("/api/"):
            if self.command not in ("GET",):
                self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
                return
            self._serve_static(path)
            return

        # parts[0] == "api"
        if len(parts) == 1:
            self._send_json(HTTPStatus.OK, {"status": "ok", "service": "hosty"})
            return

        route = parts[1]
        if route == "auth":
            self._handle_auth(parts[2:], split.query)
        elif route == "servers":
            self._handle_servers(parts[2:], split)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ------------------------------------------------------------------
    # auth

    def _handle_auth(self, parts: list[str], split: Any) -> None:
        if self.command == "POST" and parts and parts[0] == "login":
            body = self._read_json_body()
            candidate = str(body.get("token", ""))
            if self._token_matches(candidate):
                self._send_json(HTTPStatus.OK, {"ok": True}, self._cookie_headers(self.server.token))
            else:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid token"})
            return

        if self.command == "POST" and parts and parts[0] == "logout":
            self._send_json(HTTPStatus.OK, {"ok": True}, self._cookie_headers(""))
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_servers(self, parts: list[str], split: Any) -> None:
        if self.command == "GET":
            if not parts:
                if not self._require_auth(split):
                    return
                self._send_json(HTTPStatus.OK, {"servers": self.server.manager.servers_status()})
                return
            if len(parts) == 1:
                if not self._require_auth(split):
                    return
                status = self.server.manager.server_status(parts[0])
                if status is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "server not found"})
                    return
                self._send_json(HTTPStatus.OK, status)
                return
            if len(parts) == 2 and parts[1] == "stream":
                if not self._require_auth(split):
                    return
                self._stream_server(parts[0])
                return

        if self.command == "POST":
            if len(parts) < 2:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._require_auth(split):
                return
            self._post_server_action(parts)
            return

        self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    def _post_server_action(self, parts: list[str]) -> None:
        server_id = parts[0]
        action = parts[1]
        manager = self.server.manager

        if manager.server_status(server_id) is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "server not found"})
            return

        if action == "start":
            ok, error = manager.start_server(server_id)
            if ok:
                self._send_json(HTTPStatus.OK, {"ok": True, "status": manager.server_status(server_id)})
            elif error and error.get("kind") == "port-conflict":
                self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": error})
            else:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": error})
            return

        if action == "stop":
            self._send_json(HTTPStatus.OK, {"ok": manager.stop_server(server_id)})
            return

        if action == "kill":
            self._send_json(HTTPStatus.OK, {"ok": manager.kill_server(server_id)})
            return

        if action == "command":
            body = self._read_json_body()
            command = str(body.get("command", "")).strip()
            if not command:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing command"})
                return
            process = manager.get_process(server_id)
            if process:
                process.send_command(command)
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ------------------------------------------------------------------
    # SSE live stream

    def _stream_server(self, server_id: str) -> None:
        manager = self.server.manager
        if manager.server_status(server_id) is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "server not found"})
            return

        process = manager.get_process(server_id)
        if process is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "server not found"})
            return

        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def on_output(_proc: Any, text: str) -> None:
            events.put({"type": "output", "line": text})

        def on_status(_proc: Any, status: str) -> None:
            events.put(
                {
                    "type": "status",
                    "status": status,
                    "pid": _proc.pid,
                    "player_count": _proc.player_count,
                    "max_players": _proc.max_players,
                }
            )

        def on_players(_proc: Any, count: int, maximum: int) -> None:
            events.put({"type": "players", "count": count, "max": maximum})

        handlers = [
            process.connect("output-received", on_output),
            process.connect("status-changed", on_status),
            process.connect("players-changed", on_players),
        ]

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()

            # Events may have queued between subscribing and the snapshot
            # below; fold them into the history so the init replay is exact
            # (no duplicates, no loss), then send it.
            snapshot = list(process.log_history)
            drained = []
            while True:
                try:
                    drained.append(events.get_nowait())
                except queue.Empty:
                    break
            for event in drained:
                if event["type"] == "output":
                    snapshot.append(event["line"])

            self._write_sse(
                {
                    "type": "init",
                    "status": process.status,
                    "pid": process.pid,
                    "player_count": process.player_count,
                    "max_players": process.max_players,
                    "history": snapshot,
                }
            )
            # A lone large write can leave its final bytes stuck in the
            # kernel send buffer until more data follows; flush with a
            # small comment event right away.
            self._write(b": connected\n\n")

            while True:
                try:
                    message = events.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    if not self._write(b": keepalive\n\n"):
                        break
                    continue
                if not self._write_sse(message):
                    break
        finally:
            for handler_id in handlers:
                process.disconnect(handler_id)

    def _write_sse(self, payload: dict[str, Any]) -> bool:
        raw = b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"
        # Write in small chunks so a full kernel send buffer can never
        # stall the tail bytes of a large event (observed on loopback).
        for i in range(0, len(raw), 512):
            if not self._write(raw[i : i + 512]):
                return False
        return True

    def _write(self, raw: bytes) -> bool:
        try:
            view = memoryview(raw)
            while view:
                sent = self.wfile.write(view)
                if sent <= 0:
                    return False
                view = view[sent:]
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    # ------------------------------------------------------------------
    # static UI

    def _serve_static(self, request_path: str) -> None:
        resolved = resolve_static(request_path)
        if resolved is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        path, content_type = resolved
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._send_bytes(HTTPStatus.OK, content_type, data)

    # ------------------------------------------------------------------
    # auth helpers

    def _token_matches(self, candidate: str) -> bool:
        expected = self.server.token
        if not expected or not candidate:
            return False
        if not isinstance(candidate, str):
            return False
        return hmac.compare_digest(bytes(candidate, encoding="utf-8"), bytes(expected, encoding="utf-8"))

    @staticmethod
    def _cookie_headers(token: str) -> dict[str, str]:
        if token:
            value = f"{token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
        else:
            value = "; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        return {"Set-Cookie": f"{COOKIE_NAME}={value}"}

    def _require_auth(self, split: Any) -> bool:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            candidate = header[len("Bearer ") :].strip()
            if self._token_matches(candidate):
                return True
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized", "code": "auth"})
            return False

        query_token = parse_qs(split.query).get("token", [""])[0]
        if query_token and self._token_matches(query_token):
            return True

        cookie = self.headers.get("Cookie", "")
        for chunk in cookie.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == COOKIE_NAME and self._token_matches(value):
                return True

        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized", "code": "auth"})
        return False

    # ------------------------------------------------------------------
    # response helpers

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _send_json(
        self, status: HTTPStatus, payload: dict[str, Any], extra_headers: dict[str, str] | None = None
    ) -> None:
        self._send_bytes(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"), extra_headers)

    def _send_bytes(
        self, status: HTTPStatus, content_type: str, data: bytes, extra_headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
