"""
JavaManager - Detect, download, and manage JRE installations.
Uses the Adoptium API for downloading JREs.
"""
import subprocess
import tarfile
import zipfile
import shutil
import threading
import re
import sys
from pathlib import Path
from typing import Optional, Callable

import requests

from hosty.shared.utils.constants import (
    JRES_DIR, get_adoptium_jre_download_info, get_required_java_version
)


class JavaManager:
    """Manages Java Runtime Environment installations for Minecraft servers."""
    
    def __init__(self):
        self._system_java_version: Optional[int] = None
        self._detect_system_java()
    
    def _detect_system_java(self):
        """Detect the system-installed Java version."""
        try:
            result = subprocess.run(
                ["java", "-version"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stderr + result.stdout
            match = re.search(r'version "([\d\.]+)', output)
            if not match:
                self._system_java_version = None
                return

            version_text = match.group(1)
            parts = version_text.split(".")
            major = int(parts[0])

            # Java 8 reports "1.8...", while modern Java reports "17...", "21...", etc.
            if major == 1 and len(parts) > 1:
                major = int(parts[1])

            self._system_java_version = major
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._system_java_version = None
    
    @property
    def system_java_version(self) -> Optional[int]:
        """The major version of system-installed Java, or None."""
        return self._system_java_version
    
    def get_java_path(self, java_version: int) -> Optional[str]:
        """
        Get the path to a java binary for the given major version.
        Checks managed JREs first, then falls back to system Java.
        """
        # Check managed JRE
        managed_path = self._get_managed_java_path(java_version)
        if managed_path:
            return managed_path
        
        # Fall back to system java if it matches
        if self._system_java_version and self._system_java_version >= java_version:
            return shutil.which("java")
        
        return None
    
    def _get_managed_java_path(self, java_version: int) -> Optional[str]:
        """Get path to a managed JRE binary."""
        jre_dir = JRES_DIR / f"jre-{java_version}"
        if not jre_dir.exists():
            return None

        exe_name = "java.exe" if sys.platform == "win32" else "java"
        
        # Find the java binary inside the extracted directory
        # Adoptium extracts to a subdirectory like jdk-25+36-jre/
        for child in jre_dir.iterdir():
            if child.is_dir():
                java_bin = child / "bin" / exe_name
                if java_bin.exists():
                    return str(java_bin)
        
        # Direct check
        java_bin = jre_dir / "bin" / exe_name
        if java_bin.exists():
            return str(java_bin)
        
        return None
    
    def is_java_available(self, java_version: int) -> bool:
        """Check if a specific Java version is available."""
        return self.get_java_path(java_version) is not None
    
    def get_java_for_mc(self, mc_version: str) -> Optional[str]:
        """Get the java binary path appropriate for a Minecraft version."""
        java_ver = get_required_java_version(mc_version)
        return self.get_java_path(java_ver)
    
    def download_jre(self, java_version: int,
                     progress_callback: Optional[Callable[[float, str], None]] = None,
                     done_callback: Optional[Callable[[bool, str], None]] = None):
        """
        Download a JRE from Adoptium in a background thread.
        
        Args:
            java_version: The major Java version to download (e.g. 21, 25).
            progress_callback: Called with (fraction, message) on progress. Called on the thread.
            done_callback: Called with (success, message) when done. Called on the thread.
        """
        thread = threading.Thread(
            target=self._download_jre_thread,
            args=(java_version, progress_callback, done_callback),
            daemon=True
        )
        thread.start()
        return thread
    
    def _download_jre_thread(self, java_version: int,
                              progress_callback, done_callback):
        """Background thread for JRE download."""
        try:
            url, archive_type = get_adoptium_jre_download_info(java_version)
            jre_dir = JRES_DIR / f"jre-{java_version}"
            if archive_type == "zip":
                archive_path = JRES_DIR / f"jre-{java_version}.zip"
            else:
                archive_path = JRES_DIR / f"jre-{java_version}.tar.gz"
            
            if progress_callback:
                progress_callback(0.0, f"Downloading JRE {java_version}...")
            
            # Download with progress
            response = requests.get(url, stream=True, timeout=60,
                                     allow_redirects=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(archive_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and progress_callback:
                        frac = downloaded / total_size * 0.7  # 70% for download
                        size_mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        progress_callback(
                            frac,
                            f"Downloading JRE {java_version}... "
                            f"{size_mb:.1f}/{total_mb:.1f} MB"
                        )
            
            if progress_callback:
                progress_callback(0.75, f"Extracting JRE {java_version}...")
            
            # Extract
            if jre_dir.exists():
                shutil.rmtree(jre_dir)
            jre_dir.mkdir(parents=True, exist_ok=True)
            
            if archive_type == "zip":
                with zipfile.ZipFile(archive_path, 'r') as archive:
                    archive.extractall(path=jre_dir)
            else:
                with tarfile.open(archive_path, 'r:gz') as archive:
                    archive.extractall(path=jre_dir)
            
            # Clean up downloaded archive
            archive_path.unlink(missing_ok=True)
            
            # Verify
            java_path = self._get_managed_java_path(java_version)
            if java_path:
                # Make executable
                if sys.platform != "win32":
                    Path(java_path).chmod(0o755)
                if progress_callback:
                    progress_callback(1.0, f"JRE {java_version} ready")
                if done_callback:
                    done_callback(True, f"JRE {java_version} installed successfully")
            else:
                if done_callback:
                    done_callback(False, f"JRE {java_version} extraction failed: java binary not found")
                    
        except Exception as e:
            if done_callback:
                done_callback(False, f"Failed to download JRE {java_version}: {e}")
    
    def download_jre_sync(self, java_version: int,
                           progress_callback=None) -> tuple[bool, str]:
        """Synchronous JRE download. Returns (success, message)."""
        result = [False, ""]
        
        def on_done(success, msg):
            result[0] = success
            result[1] = msg
        
        thread = self.download_jre(java_version, progress_callback, on_done)
        thread.join()
        return tuple(result)
