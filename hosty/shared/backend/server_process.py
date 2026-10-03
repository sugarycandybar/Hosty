"""
ServerProcess - Manage a Minecraft server subprocess.
Handles stdin/stdout/stderr piping and lifecycle management.
"""

import re
import subprocess
import sys
import threading
from pathlib import Path

from hosty.shared.core.events import EventEmitter
from hosty.shared.utils.constants import ServerStatus
from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs


class ServerProcess(EventEmitter):
    """
    Wraps a Minecraft server subprocess with lifecycle management.
    Emits signals for output and status changes.
    """

    def __init__(self, server_dir: str, java_path: str, ram_mb: int = 2048, max_players: int = 20, jvm_args: str = ""):
        super().__init__()
        self.server_dir = Path(server_dir)
        self.java_path = java_path
        self.ram_mb = ram_mb
        self.max_players = max(1, int(max_players))
        self.jvm_args = jvm_args
        self.player_count = 0
        self._process: subprocess.Popen | None = None
        self._status = ServerStatus.STOPPED
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._pid: int | None = None
        self.log_history: list[str] = []
        self._stop_thread: threading.Thread | None = None
        # Generation counter: every start() bumps it. Stale background
        # threads (stop waiters, output readers) captured an older epoch and
        # must never mutate state belonging to a newer process. Without this,
        # stopping then quickly starting could orphan a process Hosty no
        # longer tracks, or kill the replacement.
        self._epoch = 0

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, value: str):
        if self._status != value:
            self._status = value
            self.emit_on_main_thread("status-changed", value)

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def process(self) -> subprocess.Popen | None:
        return self._process

    @property
    def is_running(self) -> bool:
        # STOPPING counts as running: the process still holds its ports and
        # must not be replaced or ignored until the stop completes.
        return self._status in (ServerStatus.RUNNING, ServerStatus.STARTING, ServerStatus.STOPPING)

    def _find_args_file(self, lib_version_dir: Path) -> Path | None:
        """Find the platform-appropriate Java @argfile in a loader library dir."""
        preferences = {
            "win32": ("win", "windows"),
            "darwin": ("osx", "mac"),
        }.get(sys.platform, ("unix", "linux"))
        candidates = [p for p in lib_version_dir.glob("*args.txt") if p.is_file()]

        def score(path: Path) -> int:
            name = path.name.lower()
            for priority, needle in enumerate(preferences):
                if needle in name:
                    return priority
            return len(preferences)

        return min(candidates, key=score) if candidates else None

    @staticmethod
    def _natural_key(path: Path) -> list:
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]

    def _build_launch_command(self) -> tuple[list[str] | None, str]:
        """Build the java launch command based on the installed mod loader.

        Returns (command_args, error_message). Exactly one is populated.
        """
        fabric_jar = self.server_dir / "fabric-server-launch.jar"
        if fabric_jar.exists():
            return ["-jar", "fabric-server-launch.jar", "nogui"], ""

        paper_jar = self.server_dir / "paper-server.jar"
        if paper_jar.exists():
            return ["-jar", "paper-server.jar", "nogui"], ""

        # Arclight: hybrid Bukkit + mods server, launched directly like Paper
        arclight_jar = self.server_dir / "arclight-server.jar"
        if arclight_jar.exists():
            return ["-jar", "arclight-server.jar", "nogui"], ""

        # Forge / NeoForge: launch via the installer-generated Java argfile
        # under libraries/, e.g. libraries/net/neoforged/neoforge/<ver>/
        libs_root = self.server_dir / "libraries" / "net"
        if libs_root.is_dir():
            for provider in ("neoforged", "minecraftforge"):
                provider_dir = libs_root / provider
                if not provider_dir.is_dir():
                    continue
                version_dirs = sorted(
                    (d for d in provider_dir.glob("*/*") if d.is_dir()),
                    key=self._natural_key,
                    reverse=True,
                )
                for version_dir in version_dirs:
                    args_file = self._find_args_file(version_dir)
                    if args_file:
                        cmd = []
                        user_jvm = self.server_dir / "user_jvm_args.txt"
                        if user_jvm.exists():
                            cmd.append(f"@{user_jvm}")
                        cmd.append(f"@{args_file}")
                        cmd.append("nogui")
                        return cmd, ""

        return None, (
            "[Hosty] Error: No server launch configuration found "
            "(expected fabric-server-launch.jar (Fabric), paper-server.jar (Paper), "
            "arclight-server.jar (Arclight), "
            "or a run configuration under libraries/ (Forge/NeoForge))\n"
        )

    def start(self) -> bool:
        """Start the Minecraft server.

        Only a fully stopped server can be started: launching while another
        stop is still in flight would orphan the old process (its handle gets
        overwritten) and corrupt state tracking. Callers must wait for
        STOPPING to resolve first.
        """
        if self._status != ServerStatus.STOPPED:
            return False

        launch_args, launch_error = self._build_launch_command()
        if not launch_args:
            self._emit_output(launch_error)
            return False

        if not self.java_path:
            self._emit_output("[Hosty] Error: No suitable Java runtime found\n")
            return False

        cmd = [
            self.java_path,
            f"-Xmx{self.ram_mb}M",
            f"-Xms{self.ram_mb}M",
        ]
        if self.jvm_args:
            cmd.extend(self.jvm_args.split())
        cmd.extend(launch_args)

        self._epoch += 1
        epoch = self._epoch
        self.status = ServerStatus.STARTING
        self.player_count = 0
        self._emit_players_changed()
        self._emit_output(f"[Hosty] Starting server with {self.ram_mb}MB RAM...\n")
        self._emit_output(f"[Hosty] Command: {' '.join(cmd)}\n")

        try:
            popen_kwargs = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "cwd": str(self.server_dir),
                "text": True,
                "bufsize": 1,
            }
            popen_kwargs.update(hidden_subprocess_kwargs())

            self._process = subprocess.Popen(cmd, **popen_kwargs)
            self._pid = self._process.pid

            # Start output reader thread
            self._stdout_thread = threading.Thread(target=self._read_output, args=(epoch, self._process), daemon=True)
            self._stdout_thread.start()

            return True

        except Exception as e:
            self._emit_output(f"[Hosty] Failed to start server: {e}\n")
            self.status = ServerStatus.STOPPED
            return False

    def stop(self):
        """Gracefully stop the server by sending /stop command.

        Idempotent: stopping an already-stopping server is a no-op. The
        background waiter operates on the captured process handle and epoch,
        so a later start can never be clobbered by this stop completing.
        """
        if self._status in (ServerStatus.STOPPING, ServerStatus.STOPPED) or not self._process:
            return

        proc = self._process
        epoch = self._epoch
        self.status = ServerStatus.STOPPING
        self._emit_output("[Hosty] Sending stop command...\n")
        self.send_command("stop")

        # Wait for graceful shutdown in background
        def _wait_stop():
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._emit_output("[Hosty] Server did not stop gracefully, killing...\n")
                self._kill_proc(proc)

            if epoch != self._epoch or self._process is not proc:
                # A newer start has taken over; its threads own the state now.
                return
            self._pid = None
            self.status = ServerStatus.STOPPED
            self._emit_output("[Hosty] Server stopped.\n")

        self._stop_thread = threading.Thread(target=_wait_stop, daemon=True)
        self._stop_thread.start()

    def _kill_proc(self, proc: subprocess.Popen) -> None:
        """Kill a captured process handle without touching tracked state."""
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    def kill(self):
        """Force kill the server process."""
        proc = self._process
        if not proc:
            return
        epoch = self._epoch
        self._kill_proc(proc)
        if epoch != self._epoch or self._process is not proc:
            return
        self._pid = None
        self.status = ServerStatus.STOPPED
        self.player_count = 0
        self._emit_players_changed()
        self._emit_output("[Hosty] Server killed.\n")

    def send_command(self, command: str):
        """Send a command to the server via stdin."""
        if not self._process or not self._process.stdin:
            return

        # Strip leading slash if present (server console doesn't use /)
        cmd = command.strip()
        if cmd.startswith("/"):
            cmd = cmd[1:]

        try:
            self._process.stdin.write(cmd + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_output(self, epoch: int, proc: subprocess.Popen):
        """Read stdout/stderr in a background thread."""
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            for line in iter(stdout.readline, ""):
                if not line:
                    break

                # Detect server started
                if self._status == ServerStatus.STARTING:
                    if "Done" in line and "For help" in line:
                        self.status = ServerStatus.RUNNING

                self._update_player_count_from_output(line)

                self._emit_output(line)

        except Exception:
            pass
        finally:
            # Process ended. Only reset state if no newer start has taken
            # over since this reader was spawned.
            current = epoch == self._epoch and self._process is proc
            if current and self._status != ServerStatus.STOPPED:
                self._pid = None
                self.status = ServerStatus.STOPPED
                self.player_count = 0
                self._emit_players_changed()
                self._emit_output("[Hosty] Server process ended.\n")

    def _emit_output(self, text: str):
        """Emit output signal on the main thread."""
        self.log_history.append(text)
        if len(self.log_history) > 1000:
            self.log_history.pop(0)
        self.emit_on_main_thread("output-received", text)

    def _emit_players_changed(self):
        self.emit_on_main_thread("players-changed", self.player_count, self.max_players)

    def set_max_players(self, max_players: int):
        self.max_players = max(1, int(max_players))
        if self.player_count > self.max_players:
            self.player_count = self.max_players
        self._emit_players_changed()

    def _update_player_count_from_output(self, line: str):
        list_match = re.search(r"There are\s+(\d+)\s+of a max of\s+(\d+)\s+players online", line)
        if list_match:
            self.player_count = int(list_match.group(1))
            self.max_players = max(1, int(list_match.group(2)))
            self._emit_players_changed()
            return

        if " joined the game" in line:
            self.player_count = min(self.max_players, self.player_count + 1)
            self._emit_players_changed()
            return

        if " left the game" in line:
            self.player_count = max(0, self.player_count - 1)
            self._emit_players_changed()
