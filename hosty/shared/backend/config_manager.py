"""
ConfigManager - Read/write server.properties files.
Preserves comments and ordering.
"""
from pathlib import Path
from typing import Optional
import re


class ConfigManager:
    """Manages reading and writing Minecraft server.properties files."""
    
    def __init__(self, server_dir: str | Path):
        self.server_dir = Path(server_dir)
        self.properties_path = self.server_dir / "server.properties"
        self._lines: list[str] = []
        self._properties: dict[str, str] = {}
        self._loaded = False
    
    def load(self) -> dict[str, str]:
        """Load server.properties file. Returns properties dict."""
        self._lines = []
        self._properties = {}
        
        if not self.properties_path.exists():
            self._loaded = True
            return self._properties
        
        with open(self.properties_path, "r", encoding="utf-8") as f:
            self._lines = f.readlines()
        
        for line in self._lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                match = re.match(r"^([^=]+)=(.*)", stripped)
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    self._properties[key] = value
        
        self._loaded = True
        return dict(self._properties)
    
    def save(self):
        """Save current properties back to server.properties."""
        if not self._loaded:
            self.load()
        
        # Update existing lines
        used_keys = set()
        new_lines = []
        
        for line in self._lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                match = re.match(r"^([^=]+)=(.*)", stripped)
                if match:
                    key = match.group(1).strip()
                    if key in self._properties:
                        new_lines.append(f"{key}={self._properties[key]}\n")
                        used_keys.add(key)
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        # Add any new properties not in the original file
        for key, value in self._properties.items():
            if key not in used_keys:
                new_lines.append(f"{key}={value}\n")
        
        self.server_dir.mkdir(parents=True, exist_ok=True)
        with open(self.properties_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    
    def get(self, key: str, default: str = "") -> str:
        """Get a property value."""
        if not self._loaded:
            self.load()
        return self._properties.get(key, default)
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean property."""
        val = self.get(key, str(default).lower())
        return val.lower() == "true"
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Get an integer property."""
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default
    
    def set_value(self, key: str, value) -> None:
        """Set a property value."""
        if not self._loaded:
            self.load()
        if isinstance(value, bool):
            self._properties[key] = str(value).lower()
        else:
            self._properties[key] = str(value)
    
    def get_all(self) -> dict[str, str]:
        """Get all properties."""
        if not self._loaded:
            self.load()
        return dict(self._properties)
    
    def set_eula(self, accepted: bool = True):
        """Set the EULA acceptance."""
        eula_path = self.server_dir / "eula.txt"
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write(f"# Accepted by Hosty\neula={'true' if accepted else 'false'}\n")
