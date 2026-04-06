"""
ServerProcess - Manage a Minecraft server subprocess.
Handles stdin/stdout/stderr piping and lifecycle management.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from hosty.utils.constants import ServerStatus
from hosty.core.events import EventEmitter


class ServerProcess(EventEmitter):
    """
    Wraps a Minecraft server subprocess with lifecycle management.
    Emits signals for output and status changes.
    """
    
    def __init__(self, server_dir: str, java_path: str, ram_mb: int = 2048):
        super().__init__()
        self.server_dir = Path(server_dir)
        self.java_path = java_path
        self.ram_mb = ram_mb
        self._process: Optional[subprocess.Popen] = None
        self._status = ServerStatus.STOPPED
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._pid: Optional[int] = None
    
    @property
    def status(self) -> str:
        return self._status
    
    @status.setter
    def status(self, value: str):
        if self._status != value:
            self._status = value
            self.emit_on_main_thread('status-changed', value)
    
    @property
    def pid(self) -> Optional[int]:
        return self._pid

    @property
    def process(self) -> Optional[subprocess.Popen]:
        return self._process
    
    @property
    def is_running(self) -> bool:
        return self._status in (ServerStatus.RUNNING, ServerStatus.STARTING)
    
    def start(self) -> bool:
        """Start the Minecraft server."""
        if self.is_running:
            return False
        
        launch_jar = self.server_dir / "fabric-server-launch.jar"
        if not launch_jar.exists():
            self._emit_output("[Hosty] Error: fabric-server-launch.jar not found\n")
            return False
        
        if not self.java_path:
            self._emit_output("[Hosty] Error: No suitable Java runtime found\n")
            return False
        
        cmd = [
            self.java_path,
            f"-Xmx{self.ram_mb}M",
            f"-Xms{self.ram_mb}M",
            "-jar", "fabric-server-launch.jar",
            "nogui",
        ]
        
        self.status = ServerStatus.STARTING
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
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(cmd, **popen_kwargs)
            self._pid = self._process.pid
            
            # Start output reader thread
            self._stdout_thread = threading.Thread(
                target=self._read_output,
                daemon=True
            )
            self._stdout_thread.start()
            
            return True
            
        except Exception as e:
            self._emit_output(f"[Hosty] Failed to start server: {e}\n")
            self.status = ServerStatus.STOPPED
            return False
    
    def stop(self):
        """Gracefully stop the server by sending /stop command."""
        if not self.is_running or not self._process:
            return
        
        self.status = ServerStatus.STOPPING
        self._emit_output("[Hosty] Sending stop command...\n")
        self.send_command("stop")
        
        # Wait for graceful shutdown in background
        def _wait_stop():
            try:
                self._process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._emit_output("[Hosty] Server did not stop gracefully, killing...\n")
                self.kill()
            
            self._pid = None
            self.status = ServerStatus.STOPPED
            self._emit_output("[Hosty] Server stopped.\n")
        
        threading.Thread(target=_wait_stop, daemon=True).start()
    
    def kill(self):
        """Force kill the server process."""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._pid = None
            self.status = ServerStatus.STOPPED
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
    
    def _read_output(self):
        """Read stdout/stderr in a background thread."""
        try:
            for line in iter(self._process.stdout.readline, ''):
                if not line:
                    break
                
                # Detect server started
                if self._status == ServerStatus.STARTING:
                    if "Done" in line and "For help" in line:
                        self.status = ServerStatus.RUNNING
                
                self._emit_output(line)
            
        except Exception:
            pass
        finally:
            # Process ended
            if self._status != ServerStatus.STOPPED:
                self._pid = None
                self.status = ServerStatus.STOPPED
                self._emit_output("[Hosty] Server process ended.\n")
    
    def _emit_output(self, text: str):
        """Emit output signal on the main thread."""
        self.emit_on_main_thread('output-received', text)
