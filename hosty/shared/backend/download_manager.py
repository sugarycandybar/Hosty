"""
DownloadManager - Handle Fabric installer and Minecraft server.jar downloads.
Uses the Fabric Meta API for versions and installer, and the Mojang version
manifest API for the vanilla server.jar.
"""
import threading
from pathlib import Path
from typing import Optional, Callable

import requests

from hosty.shared.utils.constants import (
    FABRIC_GAME_VERSIONS_URL,
    FABRIC_LOADER_VERSIONS_URL,
    FABRIC_INSTALLER_VERSIONS_URL,
    CACHE_DIR,
)

MOJANG_VERSION_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"


class DownloadManager:
    """Manages downloads of Fabric components and vanilla server JARs."""
    
    def __init__(self):
        self._game_versions: list[dict] = []
        self._loader_versions: list[dict] = []
        self._installer_url: Optional[str] = None
        self._installer_version: Optional[str] = None
        self._mojang_manifest: Optional[dict] = None
    
    def fetch_game_versions(self, include_snapshots: bool = False) -> list[str]:
        """
        Fetch available Minecraft game versions from Fabric Meta.
        Returns list of version strings, newest first.
        """
        try:
            resp = requests.get(FABRIC_GAME_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            self._game_versions = resp.json()
            
            versions = []
            for v in self._game_versions:
                if include_snapshots or v.get("stable", False):
                    versions.append(v["version"])
            
            return versions
        except Exception as e:
            print(f"Failed to fetch game versions: {e}")
            return []
    
    def fetch_loader_versions(self) -> list[str]:
        """Fetch available Fabric loader versions."""
        try:
            resp = requests.get(FABRIC_LOADER_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            self._loader_versions = resp.json()
            return [v["version"] for v in self._loader_versions]
        except Exception as e:
            print(f"Failed to fetch loader versions: {e}")
            return []
    
    def fetch_installer_info(self) -> tuple[Optional[str], Optional[str]]:
        """
        Fetch the latest Fabric installer URL and version.
        Returns (url, version) tuple.
        """
        try:
            resp = requests.get(FABRIC_INSTALLER_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            installers = resp.json()
            
            if installers:
                latest = installers[0]
                self._installer_url = latest.get("url")
                self._installer_version = latest.get("version")
                return self._installer_url, self._installer_version
        except Exception as e:
            print(f"Failed to fetch installer info: {e}")
        
        return None, None
    
    def download_installer(self,
                           progress_callback: Optional[Callable[[float, str], None]] = None
                           ) -> Optional[str]:
        """
        Download the Fabric installer JAR. Returns path to the downloaded file.
        Uses cache if already downloaded.
        """
        url, version = self.fetch_installer_info()
        if not url:
            return None
        
        # Check cache
        cached_jar = CACHE_DIR / f"fabric-installer-{version}.jar"
        if cached_jar.exists():
            if progress_callback:
                progress_callback(1.0, "Using cached installer")
            return str(cached_jar)
        
        try:
            if progress_callback:
                progress_callback(0.0, "Downloading Fabric installer...")
            
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            
            with open(cached_jar, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback:
                        frac = downloaded / total
                        progress_callback(
                            frac,
                            f"Downloading installer... {downloaded/1024:.0f} KB"
                        )
            
            if progress_callback:
                progress_callback(1.0, "Installer downloaded")
            
            return str(cached_jar)
            
        except Exception as e:
            print(f"Failed to download installer: {e}")
            cached_jar.unlink(missing_ok=True)
            return None
    
    # ----- Mojang vanilla server.jar download -----
    
    def _fetch_mojang_manifest(self) -> Optional[dict]:
        """Fetch the Mojang version manifest (cached per session)."""
        if self._mojang_manifest:
            return self._mojang_manifest
        try:
            resp = requests.get(MOJANG_VERSION_MANIFEST, timeout=15)
            resp.raise_for_status()
            self._mojang_manifest = resp.json()
            return self._mojang_manifest
        except Exception as e:
            print(f"Failed to fetch Mojang manifest: {e}")
            return None
    
    def _get_version_json_url(self, mc_version: str) -> Optional[str]:
        """Get the URL for a specific MC version's metadata JSON."""
        manifest = self._fetch_mojang_manifest()
        if not manifest:
            return None
        for entry in manifest.get("versions", []):
            if entry.get("id") == mc_version:
                return entry.get("url")
        return None
    
    def download_server_jar(self, mc_version: str, server_dir: str,
                             progress_callback: Optional[Callable[[float, str], None]] = None
                             ) -> tuple[bool, str]:
        """
        Download the vanilla Minecraft server.jar from Mojang into server_dir.
        
        This is required because the Fabric installer only installs the loader;
        it expects server.jar to already be present.
        
        Args:
            mc_version: Minecraft version string (e.g. "1.21.4", "26.1.1")
            server_dir: Path to the server directory
            progress_callback: Optional (fraction, message) callback
            
        Returns:
            (success, message) tuple
        """
        dest = Path(server_dir) / "server.jar"
        
        # Skip if already present
        if dest.exists() and dest.stat().st_size > 1000:
            if progress_callback:
                progress_callback(1.0, "server.jar already present")
            return True, "server.jar already present"
        
        try:
            # Step 1: Get version JSON URL from manifest
            if progress_callback:
                progress_callback(0.05, f"Fetching MC {mc_version} metadata...")
            
            version_url = self._get_version_json_url(mc_version)
            if not version_url:
                return False, f"Minecraft version {mc_version} not found in Mojang manifest"
            
            # Step 2: Fetch version JSON
            if progress_callback:
                progress_callback(0.1, "Reading version details...")
            
            resp = requests.get(version_url, timeout=15)
            resp.raise_for_status()
            version_data = resp.json()
            
            # Step 3: Extract server download URL
            downloads = version_data.get("downloads", {})
            server_info = downloads.get("server")
            if not server_info:
                return False, f"No server download available for MC {mc_version}"
            
            jar_url = server_info.get("url")
            jar_size = server_info.get("size", 0)
            jar_sha1 = server_info.get("sha1", "")
            
            if not jar_url:
                return False, "server.jar URL not found in version metadata"
            
            # Step 4: Download server.jar
            if progress_callback:
                progress_callback(0.15, "Downloading server.jar...")
            
            resp = requests.get(jar_url, stream=True, timeout=120)
            resp.raise_for_status()
            
            total = int(resp.headers.get('content-length', jar_size))
            downloaded = 0
            
            Path(server_dir).mkdir(parents=True, exist_ok=True)
            
            with open(dest, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback:
                        frac = 0.15 + (downloaded / total) * 0.85
                        size_mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        progress_callback(
                            frac,
                            f"Downloading server.jar... {size_mb:.1f}/{total_mb:.1f} MB"
                        )
            
            if progress_callback:
                progress_callback(1.0, "server.jar downloaded")
            
            return True, "server.jar downloaded successfully"
            
        except Exception as e:
            # Clean up partial download
            dest.unlink(missing_ok=True)
            return False, f"Failed to download server.jar: {e}"
    
    # ----- Fabric installation -----
    
    def install_fabric_server(self, java_path: str, installer_jar: str,
                               mc_version: str, server_dir: str,
                               loader_version: Optional[str] = None,
                               progress_callback: Optional[Callable[[float, str], None]] = None
                               ) -> tuple[bool, str]:
        """
        Run the Fabric installer to set up a server.
        
        Args:
            java_path: Path to the java binary.
            installer_jar: Path to the Fabric installer JAR.
            mc_version: Minecraft version string.
            server_dir: Directory to install the server into.
            loader_version: Optional specific loader version.
            progress_callback: Progress callback.
        
        Returns:
            (success, message) tuple.
        """
        import subprocess
        
        Path(server_dir).mkdir(parents=True, exist_ok=True)
        
        cmd = [
            java_path, "-jar", installer_jar,
            "server",
            "-mcversion", mc_version,
            "-dir", server_dir,
        ]
        
        if loader_version:
            cmd.extend(["-loader", loader_version])
        
        if progress_callback:
            progress_callback(0.5, f"Installing Fabric server for MC {mc_version}...")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=server_dir,
            )
            
            if result.returncode == 0:
                # Verify the launch jar exists
                launch_jar = Path(server_dir) / "fabric-server-launch.jar"
                if launch_jar.exists():
                    if progress_callback:
                        progress_callback(1.0, "Fabric server installed successfully")
                    return True, "Installation successful"
                else:
                    return False, "Installation completed but fabric-server-launch.jar not found"
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                return False, f"Installation failed: {error_msg}"
                
        except subprocess.TimeoutExpired:
            return False, "Installation timed out (5 minutes)"
        except Exception as e:
            return False, f"Installation error: {e}"
    
    def fetch_all_versions_async(self,
                                  callback: Callable[[list[str], list[str]], None]):
        """
        Fetch game and loader versions in a background thread.
        Calls callback(game_versions, loader_versions) when done.
        """
        def _fetch():
            games = self.fetch_game_versions()
            loaders = self.fetch_loader_versions()
            callback(games, loaders)
        
        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()
        return thread
