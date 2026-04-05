"""
ServerManager - CRUD operations for server instances.
Handles persistence, creation workflow, and server lifecycle.
"""
import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from hosty.utils.constants import (
    SERVERS_DIR, CONFIG_FILE, DEFAULT_RAM_MB, DEFAULT_SERVER_PROPERTIES,
    get_required_java_version,
)
from hosty.backend.server_process import ServerProcess
from hosty.backend.config_manager import ConfigManager
from hosty.backend.java_manager import JavaManager
from hosty.backend.download_manager import DownloadManager
from hosty.core.events import EventEmitter


class ServerInfo:
    """Data class for server metadata."""
    
    def __init__(self, data: dict):
        self.id: str = data.get("id", str(uuid.uuid4()))
        self.name: str = data.get("name", "Unnamed Server")
        self.mc_version: str = data.get("mc_version", "")
        self.loader_version: str = data.get("loader_version", "")
        self.ram_mb: int = data.get("ram_mb", DEFAULT_RAM_MB)
        self.java_version: int = data.get("java_version", 21)
        self.icon_path: str = data.get("icon_path", "")
        self.created_at: str = data.get("created_at", datetime.now().isoformat())
        self.path: str = data.get("path", "")
    
    @property
    def server_dir(self) -> Path:
        """Get the server directory path."""
        if self.path:
            return Path(self.path)
        return SERVERS_DIR / self.id
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mc_version": self.mc_version,
            "loader_version": self.loader_version,
            "ram_mb": self.ram_mb,
            "java_version": self.java_version,
            "icon_path": self.icon_path,
            "created_at": self.created_at,
            "path": str(self.server_dir),
        }


class ServerManager(EventEmitter):
    """
    Manages all server instances: CRUD, persistence, and process management.
    """
    
    def __init__(self):
        super().__init__()
        self._servers: dict[str, ServerInfo] = {}
        self._processes: dict[str, ServerProcess] = {}
        self.java_manager = JavaManager()
        self.download_manager = DownloadManager()
        self._load()
    
    def _load(self):
        """Load servers from persisted JSON."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                for entry in data.get("servers", []):
                    info = ServerInfo(entry)
                    self._servers[info.id] = info
            except Exception as e:
                print(f"Failed to load servers: {e}")
    
    def _save(self):
        """Persist servers to JSON."""
        data = {
            "servers": [s.to_dict() for s in self._servers.values()]
        }
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Failed to save servers: {e}")
    
    @property
    def servers(self) -> list[ServerInfo]:
        """Get all servers sorted by creation date."""
        return sorted(self._servers.values(), key=lambda s: s.created_at)
    
    def get_server(self, server_id: str) -> Optional[ServerInfo]:
        """Get a server by ID."""
        return self._servers.get(server_id)
    
    def add_server(self, name: str, mc_version: str, loader_version: str = "",
                   ram_mb: int = DEFAULT_RAM_MB) -> ServerInfo:
        """
        Create and register a new server.
        Does NOT install Fabric — call install_server() separately.
        """
        server_id = str(uuid.uuid4())
        java_ver = get_required_java_version(mc_version)
        
        info = ServerInfo({
            "id": server_id,
            "name": name,
            "mc_version": mc_version,
            "loader_version": loader_version,
            "ram_mb": ram_mb,
            "java_version": java_ver,
            "path": str(SERVERS_DIR / server_id),
        })
        
        # Create server directory
        info.server_dir.mkdir(parents=True, exist_ok=True)
        
        self._servers[server_id] = info
        self._save()
        self.emit_on_main_thread('server-added', server_id)
        
        return info
    
    def rename_server(self, server_id: str, new_name: str):
        """Rename a server."""
        info = self._servers.get(server_id)
        if info:
            info.name = new_name
            self._save()
            self.emit_on_main_thread('server-changed', server_id)
    
    def set_server_icon(self, server_id: str, icon_path: str):
        """Set the icon for a server."""
        info = self._servers.get(server_id)
        if info:
            info.icon_path = icon_path
            self._save()
            self.emit_on_main_thread('server-changed', server_id)
    
    def update_server_ram(self, server_id: str, ram_mb: int):
        """Update RAM allocation for a server."""
        info = self._servers.get(server_id)
        if info:
            info.ram_mb = ram_mb
            self._save()
            proc = self._processes.get(server_id)
            if proc:
                proc.ram_mb = ram_mb
            self.emit_on_main_thread('server-changed', server_id)
    
    def delete_server(self, server_id: str, delete_files: bool = True):
        """Delete a server. Optionally delete its files."""
        info = self._servers.get(server_id)
        if not info:
            return
        
        # Stop if running
        process = self._processes.get(server_id)
        if process and process.is_running:
            process.kill()
        
        if server_id in self._processes:
            del self._processes[server_id]
        
        # Delete files
        if delete_files and info.server_dir.exists():
            shutil.rmtree(info.server_dir, ignore_errors=True)
        
        del self._servers[server_id]
        self._save()
        self.emit_on_main_thread('server-removed', server_id)
    
    def get_process(self, server_id: str) -> Optional[ServerProcess]:
        """Get or create a ServerProcess for a server."""
        info = self._servers.get(server_id)
        if not info:
            return None
        
        if server_id not in self._processes:
            java_path = self.java_manager.get_java_for_mc(info.mc_version)
            if not java_path:
                # Try system java as fallback
                java_path = shutil.which("java")
            
            self._processes[server_id] = ServerProcess(
                server_dir=str(info.server_dir),
                java_path=java_path or "java",
                ram_mb=info.ram_mb,
            )
        
        return self._processes[server_id]
    
    def get_config(self, server_id: str) -> Optional[ConfigManager]:
        """Get a ConfigManager for a server's server.properties."""
        info = self._servers.get(server_id)
        if not info:
            return None
        return ConfigManager(str(info.server_dir))
    
    def is_any_server_running(self) -> bool:
        """Check if any server is currently running."""
        return any(p.is_running for p in self._processes.values())

    def get_running_server_id(self) -> Optional[str]:
        """Return the server id whose process is running, or None."""
        for server_id, process in self._processes.items():
            if process.is_running:
                return server_id
        return None
        
    def stop_all(self):
        """Stop all running servers."""
        for server_id, process in self._processes.items():
            if process.is_running:
                process.stop()
                # Wait up to 3 seconds for graceful shutdown, then forcefully kill to prevent orphans
                try:
                    process.process.wait(timeout=3.0)
                except Exception:
                    pass
                process.kill()
