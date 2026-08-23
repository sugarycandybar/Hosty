"""
JavaManager - Detect, download, and manage JRE installations.
Uses the Adoptium API for downloading JREs.
"""

import logging
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

from hosty.shared.utils.constants import JRES_DIR, get_adoptium_jre_download_info, get_required_java_version
from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


def _is_valid_jre_archive(path: Path, archive_type: str) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < 1024:
            return False
        with open(path, "rb") as f:
            magic = f.read(4)
        if archive_type == "zip":
            return magic.startswith(b"PK")
        return magic.startswith(b"\x1f\x8b")
    except OSError:
        return False


def _verify_java_binary(java_path: str, expected_major: int) -> bool:
    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            return False
        output = result.stderr + result.stdout
        match = re.search(r'version "([\d\.]+)', output)
        if not match:
            return False
        version_text = match.group(1)
        parts = version_text.split(".")
        try:
            major = int(parts[0])
        except ValueError:
            return False
        if major == 1 and len(parts) > 1:
            try:
                major = int(parts[1])
            except ValueError:
                return False
        return major >= expected_major
    except Exception:
        return False


def _classify_jre_download_error(exc: Exception) -> str | None:
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status == 404:
            return None  # caller will format specific message
        if status == 429:
            return _("rate limited by Adoptium/GitHub, please retry in a moment")
        if status in (502, 503, 504):
            return _("Adoptium/GitHub is temporarily unavailable (HTTP {})").format(status)
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return _("network error: {}").format(exc)
    return None


class JavaManager:
    """Manages Java Runtime Environment installations for Minecraft servers."""

    def __init__(self):
        self._system_java_version: int | None = None
        self._system_java_checked = False
        self._download_lock = threading.Lock()
        self._in_flight_downloads: set[int] = set()

    def _ensure_system_java_detected(self):
        """Detect system Java on first use to keep app startup fast."""
        if self._system_java_checked:
            return
        self._detect_system_java()

    def _detect_system_java(self):
        """Detect the system-installed Java version."""
        self._system_java_checked = True
        try:
            result = subprocess.run(
                ["java", "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                **hidden_subprocess_kwargs(),
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
    def system_java_version(self) -> int | None:
        """The major version of system-installed Java, or None."""
        self._ensure_system_java_detected()
        return self._system_java_version

    def get_java_path(self, java_version: int) -> str | None:
        """
        Get the path to a java binary for the given major version.
        Checks managed JREs first, then falls back to system Java.
        """
        # Check managed JRE
        managed_path = self._get_managed_java_path(java_version)
        if managed_path:
            return managed_path

        # Fall back to system java if it matches
        self._ensure_system_java_detected()
        if self._system_java_version and self._system_java_version >= java_version:
            return shutil.which("java")

        return None

    def _get_managed_java_path(self, java_version: int) -> str | None:
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

    def get_java_for_mc(self, mc_version: str) -> str | None:
        """Get the java binary path appropriate for a Minecraft version."""
        java_ver = get_required_java_version(mc_version)
        return self.get_java_path(java_ver)

    def download_jre(
        self,
        java_version: int,
        progress_callback: Callable[[float, str], None] | None = None,
        done_callback: Callable[[bool, str], None] | None = None,
    ):
        """
        Download a JRE from Adoptium in a background thread.

        Args:
            java_version: The major Java version to download (e.g. 21, 25).
            progress_callback: Called with (fraction, message) on progress. Called on the thread.
            done_callback: Called with (success, message) when done. Called on the thread.
        """
        with self._download_lock:
            if java_version in self._in_flight_downloads:
                if done_callback:
                    done_callback(False, _("Java {} download already in progress").format(java_version))
                return None
            self._in_flight_downloads.add(java_version)

        thread = threading.Thread(
            target=self._download_jre_thread, args=(java_version, progress_callback, done_callback), daemon=True
        )
        thread.start()
        return thread

    def _download_jre_thread(self, java_version: int, progress_callback, done_callback):
        """Background thread for JRE download."""
        try:
            self._download_jre_impl(java_version, progress_callback, done_callback)
        finally:
            with self._download_lock:
                self._in_flight_downloads.discard(java_version)

    def _download_jre_impl(self, java_version: int, progress_callback, done_callback):
        """Actual JRE download work (runs on the download thread)."""

        def _do_download() -> None:
            url, archive_type = get_adoptium_jre_download_info(java_version)
            jre_dir = JRES_DIR / f"jre-{java_version}"
            if archive_type == "zip":
                archive_path = JRES_DIR / f"jre-{java_version}.zip"
            else:
                archive_path = JRES_DIR / f"jre-{java_version}.tar.gz"

            if progress_callback:
                progress_callback(0.0, _("Downloading JRE {}...").format(java_version))

            # Ensure parent dir exists before writing
            JRES_DIR.mkdir(parents=True, exist_ok=True)

            # Download with one automatic retry for transient failures
            last_exc: Exception | None = None
            dl_total_size = 0
            for attempt in range(2):
                try:
                    response = requests.get(url, stream=True, timeout=60, allow_redirects=True)
                    response.raise_for_status()

                    dl_total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0

                    with open(archive_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            if dl_total_size > 0 and progress_callback:
                                frac = downloaded / dl_total_size * 0.7  # 70% for download
                                size_mb = downloaded / (1024 * 1024)
                                total_mb = dl_total_size / (1024 * 1024)
                                progress_callback(
                                    frac,
                                    _("Downloading JRE {}... {:.1f}/{:.1f} MB").format(java_version, size_mb, total_mb),
                                )
                    break
                except Exception as e:
                    last_exc = e
                    # 404 means the binary genuinely doesn't exist — don't retry
                    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                        if e.response.status_code == 404:
                            raise
                    if attempt == 0:
                        classified = _classify_jre_download_error(e)
                        # Retry on network/transient errors
                        if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)) or (
                            isinstance(e, requests.exceptions.HTTPError)
                            and e.response is not None
                            and e.response.status_code in (429, 502, 503, 504)
                        ):
                            logger.warning("JRE %s download transient failure (attempt 1/2): %s", java_version, e)
                            time.sleep(2)
                            continue
                        if classified is not None and attempt == 0:
                            # still retry once for classified transient errors
                            time.sleep(2)
                            continue
                    raise last_exc  # type: ignore[misc]

            # Validate archive before extraction
            if not _is_valid_jre_archive(archive_path, archive_type):
                archive_path.unlink(missing_ok=True)
                raise RuntimeError(_("downloaded archive is invalid or corrupted"))

            if progress_callback:
                progress_callback(0.75, _("Extracting JRE {}...").format(java_version))

            # Extract with cleanup on failure
            try:
                if jre_dir.exists():
                    shutil.rmtree(jre_dir)
                jre_dir.mkdir(parents=True, exist_ok=True)

                if archive_type == "zip":
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        archive.extractall(path=jre_dir)
                else:
                    with tarfile.open(archive_path, "r:gz") as archive:
                        archive.extractall(path=jre_dir)
            except Exception:
                shutil.rmtree(jre_dir, ignore_errors=True)
                archive_path.unlink(missing_ok=True)
                raise

            # Clean up downloaded archive
            archive_path.unlink(missing_ok=True)

            # Verify java binary exists and actually runs
            java_path = self._get_managed_java_path(java_version)
            if not java_path:
                shutil.rmtree(jre_dir, ignore_errors=True)
                raise RuntimeError(_("JRE {} extraction failed: java binary not found").format(java_version))
            if sys.platform != "win32":
                try:
                    Path(java_path).chmod(0o755)
                except OSError:
                    pass
            if not _verify_java_binary(java_path, java_version):
                shutil.rmtree(jre_dir, ignore_errors=True)
                raise RuntimeError(_("JRE {} installed but failed verification; please retry").format(java_version))

            if progress_callback:
                progress_callback(1.0, _("JRE {} ready").format(java_version))
            if done_callback:
                done_callback(True, _("JRE {} installed successfully").format(java_version))

        try:
            _do_download()
        except Exception as e:
            classified = _classify_jre_download_error(e)
            is_404 = (
                isinstance(e, requests.exceptions.HTTPError)
                and e.response is not None
                and e.response.status_code == 404
            )
            if is_404:
                msg = _(
                    "Adoptium has no JRE for Java {} (HTTP 404). Check your internet or try a different Java version."
                ).format(java_version)
            elif classified is not None:
                msg = classified
            else:
                msg = str(e)
            if done_callback:
                done_callback(False, _("Failed to download JRE {}: {}").format(java_version, msg))

    def download_jre_sync(self, java_version: int, progress_callback=None) -> tuple[bool, str]:
        """Synchronous JRE download. Returns (success, message)."""
        result = [False, ""]

        def on_done(success, msg):
            result[0] = success
            result[1] = msg

        thread = self.download_jre(java_version, progress_callback, on_done)
        if thread is not None:
            thread.join()
        return tuple(result)
