"""
DownloadManager - Handle mod-loader installers and Minecraft server.jar downloads.
Uses the Fabric Meta API, Forge promotions API, and NeoForge maven API for
loader versions/installers, and the Mojang version manifest API for the
vanilla server.jar.
"""

import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path

import requests

from hosty.shared.utils.constants import (
    CACHE_DIR,
    FABRIC_GAME_VERSIONS_URL,
    FABRIC_INSTALLER_VERSIONS_URL,
    FABRIC_LOADER_VERSIONS_URL,
    FORGE_MAVEN_INSTALLER_URL,
    FORGE_PROMOTIONS_URL,
    HOSTY_USER_AGENT,
    LOADER_FABRIC,
    LOADER_FORGE,
    LOADER_NEOFORGE,
    LOADER_PAPER,
    NEOFORGE_MAVEN_INSTALLER_URL,
    NEOFORGE_VERSIONS_URL,
    PAPER_BUILD_URL,
    PAPER_LATEST_BUILD_URL,
    mod_loader_name,
    normalize_loader_type,
)
from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs

MOJANG_VERSION_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"

logger = logging.getLogger(__name__)


def _is_valid_jar(path: Path, min_size: int = 50 * 1024) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < min_size:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"PK"
    except OSError:
        return False


class DownloadManager:
    """Manages downloads of mod-loader components and vanilla server JARs."""

    def __init__(self):
        self._game_versions: list[dict] = []
        self._loader_versions: list[dict] = []
        self._installer_url: str | None = None
        self._installer_version: str | None = None
        self._mojang_manifest: dict | None = None
        self._loader_build_cache: dict[tuple[str, str, bool], str] = {}

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
            logger.warning("Failed to fetch game versions: %s", e)
            return []

    def fetch_loader_versions(self) -> list[str]:
        """Fetch available Fabric loader versions (newest first)."""
        try:
            resp = requests.get(FABRIC_LOADER_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            self._loader_versions = resp.json()
            return [v["version"] for v in self._loader_versions]
        except Exception as e:
            logger.warning("Failed to fetch loader versions: %s", e)
            return []

    @staticmethod
    def _neoforge_branch_segments(mc_version: str) -> list[int]:
        """Map a Minecraft version to its NeoForge version branch segments.

        Old scheme: MC 1.X.Y -> NeoForge X.Y.z (e.g. 1.21.4 -> 21.4.x, with an
        implicit .0 for bare minors: 1.21 -> 21.0.x). New scheme (26.1+):
        NeoForge mirrors the MC version (26.1.2 -> 26.1.2.x, 26.2 -> 26.2.0.x).
        Returns [] when unmappable.
        """
        parts = str(mc_version or "").strip().split(".")
        if not parts or not all(p.isdigit() for p in parts):
            return []
        nums = [int(p) for p in parts]
        if nums[0] == 1:
            nums = nums[1:]
            if len(nums) == 1:
                nums.append(0)
        return nums

    @staticmethod
    def _fetch_paper_build(mc_version: str, build: str | None = None) -> dict:
        """Fetch PaperMC Fill metadata for a build (latest when build is falsy).

        Returns the build JSON dict; raises on any failure.
        """
        if build:
            url = PAPER_BUILD_URL.format(mc=mc_version, build=int(build))
        else:
            url = PAPER_LATEST_BUILD_URL.format(mc=mc_version)
        resp = requests.get(url, headers={"User-Agent": HOSTY_USER_AGENT}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        downloads = (data.get("downloads") or {}).get("server:default") or {}
        if not downloads.get("url"):
            raise ValueError("Paper build has no server download")
        return data

    def resolve_loader_build(
        self,
        loader_type: str,
        mc_version: str | None = None,
        include_snapshots: bool = False,
    ) -> str:
        """Return the preferred loader build for a loader type.

        For Fabric this is the newest global loader version. For Forge the
        recommended promotion is preferred over latest. For NeoForge the
        newest build on the selected Minecraft version's branch is returned.

        Returns "" when no suitable build can be resolved.
        """
        loader_type = normalize_loader_type(loader_type)
        mc_version = str(mc_version or "").strip()

        cache_key = (loader_type, mc_version, bool(include_snapshots))
        if cache_key in self._loader_build_cache:
            return self._loader_build_cache[cache_key]

        if loader_type == LOADER_FABRIC:
            versions = self.fetch_loader_versions()
            result = versions[0] if versions else ""
            if result:
                self._loader_build_cache[cache_key] = result
            return result

        if loader_type == LOADER_FORGE:
            try:
                resp = requests.get(FORGE_PROMOTIONS_URL, timeout=15)
                resp.raise_for_status()
                promos = resp.json().get("promos", {})
            except Exception as e:
                logger.warning("Failed to fetch Forge promotions: %s", e)
                return ""
            for key in (f"{mc_version}-recommended", f"{mc_version}-latest"):
                build = str(promos.get(key) or "").strip()
                if build:
                    self._loader_build_cache[cache_key] = build
                    return build
            return ""

        if loader_type == LOADER_NEOFORGE:
            branch = self._neoforge_branch_segments(mc_version)
            if not branch:
                return ""
            try:
                resp = requests.get(NEOFORGE_VERSIONS_URL, timeout=15)
                resp.raise_for_status()
                versions = resp.json().get("versions", [])
            except Exception as e:
                logger.warning("Failed to fetch NeoForge versions: %s", e)
                return ""
            candidates = []
            beta_candidates = []
            suffix_re = re.compile(r"^(?P<base>\d+(?:\.\d+)*)(?P<tag>-\w+)?$")
            for v in versions:
                m = suffix_re.match(str(v))
                if not m:
                    continue
                base_nums = [int(p) for p in m.group("base").split(".")]
                # Match on whole segments so branch 21.4 never picks 21.11.x
                if base_nums[: len(branch)] != branch:
                    continue
                is_beta = "beta" in (m.group("tag") or "").lower()
                entry = (tuple(base_nums), str(v))
                if is_beta:
                    beta_candidates.append(entry)
                else:
                    candidates.append(entry)
            # Prefer stable builds; fall back to betas when the branch has none
            pool = candidates or beta_candidates
            if not pool:
                return ""
            pool.sort(key=lambda item: item[0])
            result = pool[-1][1]
            self._loader_build_cache[cache_key] = result
            return result

        if loader_type == LOADER_PAPER:
            if not mc_version:
                return ""
            try:
                # Prefer the newest STABLE build; the /latest endpoint may
                # return an EXPERIMENTAL build during pre-release windows.
                try:
                    resp = requests.get(
                        f"{PAPER_FILL_API_BASE}/projects/paper/versions/{mc_version}/builds",
                        headers={"User-Agent": HOSTY_USER_AGENT},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    builds = resp.json()
                    # /builds returns a bare list; be defensive about wrappers
                    if isinstance(builds, dict):
                        builds = builds.get("builds") or builds.get("data") or []
                    if isinstance(builds, list) and builds:
                        stable_ids = [
                            int(b.get("id"))
                            for b in builds
                            if b.get("channel") == "STABLE" and str(b.get("id")).isdigit()
                        ]
                        if stable_ids:
                            result = str(max(stable_ids))
                            self._loader_build_cache[cache_key] = result
                            return result
                except Exception:
                    pass
                result = str(self._fetch_paper_build(mc_version).get("id") or "")
                if result:
                    self._loader_build_cache[cache_key] = result
                return result
            except Exception as e:
                logger.warning("Failed to fetch Paper builds: %s", e)
                return ""

        return ""

    def resolve_loader_build_async(
        self,
        loader_type: str,
        mc_version: str | None,
        callback: Callable[[str], None],
    ):
        """Resolve a loader build in a background thread; calls callback(build) when done."""
        thread = threading.Thread(
            target=lambda: callback(self.resolve_loader_build(loader_type, mc_version)),
            daemon=True,
        )
        thread.start()
        return thread

    def fetch_installer_info(self) -> tuple[str | None, str | None]:
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
            logger.warning("Failed to fetch installer info: %s", e)

        return None, None

    def download_installer(
        self,
        progress_callback: Callable[[float, str], None] | None = None,
        loader_type: str = LOADER_FABRIC,
        mc_version: str = "",
        loader_version: str | None = None,
    ) -> str | None:
        """
        Download a mod-loader installer JAR. Returns path to the downloaded file.
        Uses cache if already downloaded.

        For Fabric the newest installer is fetched from Fabric Meta (the
        installer tool itself can install any loader version). For Forge and
        NeoForge the version-specific installer is downloaded from the
        respective maven.
        """
        loader_type = normalize_loader_type(loader_type)
        cached_jar: Path | None

        if loader_type == LOADER_FABRIC:
            # The Fabric installer tool is version-independent: the newest
            # installer can install any loader version (passed via -loader).
            url, version = self.fetch_installer_info()
            if not url or not version:
                return None
            cached_jar = CACHE_DIR / f"fabric-installer-{version}.jar"
        elif loader_type == LOADER_FORGE:
            version = str(loader_version or "").strip()
            if not mc_version or not version:
                return None
            url = FORGE_MAVEN_INSTALLER_URL.format(mc=mc_version, version=version)
            cached_jar = CACHE_DIR / f"forge-installer-{mc_version}-{version}.jar"
        elif loader_type == LOADER_NEOFORGE:
            version = str(loader_version or "").strip()
            if not version:
                return None
            url = NEOFORGE_MAVEN_INSTALLER_URL.format(version=version)
            cached_jar = CACHE_DIR / f"neoforge-installer-{version}.jar"
        elif loader_type == LOADER_PAPER:
            # Paper ships a ready-to-run server jar (no installer step).
            if not mc_version:
                return None
            try:
                build = self._fetch_paper_build(mc_version, loader_version or None)
                downloads = build["downloads"]["server:default"]
                url = downloads["url"]
                cached_jar = CACHE_DIR / f"paper-{mc_version}-{build['id']}.jar"
            except Exception as e:
                logger.warning("Failed to resolve Paper jar: %s", e)
                return None
        else:
            return None

        # Check cache (validate: stale/HTML cache must not be reused)
        assert cached_jar is not None
        if _is_valid_jar(cached_jar):
            if progress_callback:
                progress_callback(1.0, _("Using cached installer"))
            return str(cached_jar)
        if cached_jar.exists():
            cached_jar.unlink(missing_ok=True)

        try:
            if progress_callback:
                progress_callback(0.0, _("Downloading {} installer...").format(mod_loader_name(loader_type)))

            CACHE_DIR.mkdir(parents=True, exist_ok=True)

            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(cached_jar, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback:
                        frac = downloaded / total
                        progress_callback(frac, _("Downloading installer... {:.0f} KB").format(downloaded / 1024))

            if not _is_valid_jar(cached_jar):
                logger.warning("Downloaded installer failed validation: %s", cached_jar)
                cached_jar.unlink(missing_ok=True)
                return None

            if progress_callback:
                progress_callback(1.0, _("Installer downloaded"))

            return str(cached_jar)

        except Exception as e:
            logger.warning("Failed to download installer: %s", e)
            cached_jar.unlink(missing_ok=True)
            return None

    # ----- Mojang vanilla server.jar download -----

    def _fetch_mojang_manifest(self) -> dict | None:
        """Fetch the Mojang version manifest (cached per session)."""
        if self._mojang_manifest:
            return self._mojang_manifest
        try:
            resp = requests.get(MOJANG_VERSION_MANIFEST, timeout=15)
            resp.raise_for_status()
            self._mojang_manifest = resp.json()
            return self._mojang_manifest
        except Exception as e:
            logger.warning("Failed to fetch Mojang manifest: %s", e)
            return None

    def _get_version_json_url(self, mc_version: str) -> str | None:
        """Get the URL for a specific MC version's metadata JSON."""
        manifest = self._fetch_mojang_manifest()
        if not manifest:
            return None
        for entry in manifest.get("versions", []):
            if entry.get("id") == mc_version:
                return entry.get("url")
        return None

    def download_server_jar(
        self, mc_version: str, server_dir: str, progress_callback: Callable[[float, str], None] | None = None
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

        # Skip if already present and valid
        if _is_valid_jar(dest, min_size=1000):
            if progress_callback:
                progress_callback(1.0, _("server.jar already present"))
            return True, _("server.jar already present")
        if dest.exists():
            dest.unlink(missing_ok=True)

        try:
            # Step 1: Get version JSON URL from manifest
            if progress_callback:
                progress_callback(0.05, _("Fetching MC {} metadata...").format(mc_version))

            version_url = self._get_version_json_url(mc_version)
            if not version_url:
                return False, _("Minecraft version {} not found in Mojang manifest").format(mc_version)

            # Step 2: Fetch version JSON
            if progress_callback:
                progress_callback(0.1, _("Reading version details..."))

            resp = requests.get(version_url, timeout=15)
            resp.raise_for_status()
            version_data = resp.json()

            # Step 3: Extract server download URL
            downloads = version_data.get("downloads", {})
            server_info = downloads.get("server")
            if not server_info:
                return False, _("No server download available for MC {}").format(mc_version)

            jar_url = server_info.get("url")
            jar_size = server_info.get("size", 0)

            if not jar_url:
                return False, _("server.jar URL not found in version metadata")

            # Step 4: Download server.jar
            if progress_callback:
                progress_callback(0.15, _("Downloading server.jar..."))

            resp = requests.get(jar_url, stream=True, timeout=120)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", jar_size))
            downloaded = 0

            Path(server_dir).mkdir(parents=True, exist_ok=True)

            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback:
                        frac = 0.15 + (downloaded / total) * 0.85
                        size_mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        progress_callback(
                            frac, _("Downloading server.jar... {:.1f}/{:.1f} MB").format(size_mb, total_mb)
                        )

            if not _is_valid_jar(dest, min_size=1000):
                dest.unlink(missing_ok=True)
                return False, _("Downloaded server.jar is invalid or corrupted; please retry")

            if progress_callback:
                progress_callback(1.0, _("server.jar downloaded"))

            return True, _("server.jar downloaded successfully")

        except Exception as e:
            # Clean up partial download
            dest.unlink(missing_ok=True)
            return False, _("Failed to download server.jar: {}").format(e)

    # ----- Loader installation -----

    def install_server(
        self,
        loader_type: str,
        java_path: str,
        installer_jar: str,
        mc_version: str,
        server_dir: str,
        loader_version: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        """Install the selected mod loader into server_dir. Dispatches per loader."""
        loader_type = normalize_loader_type(loader_type)
        if loader_type == LOADER_FABRIC:
            return self.install_fabric_server(
                java_path=java_path,
                installer_jar=installer_jar,
                mc_version=mc_version,
                server_dir=server_dir,
                loader_version=loader_version,
                progress_callback=progress_callback,
            )
        if loader_type == LOADER_PAPER:
            return self._install_paper_server(
                installer_jar=installer_jar,
                server_dir=server_dir,
                progress_callback=progress_callback,
            )
        return self._install_maven_loader_server(
            loader_name=mod_loader_name(loader_type),
            java_path=java_path,
            installer_jar=installer_jar,
            mc_version=mc_version,
            server_dir=server_dir,
            progress_callback=progress_callback,
        )

    def _install_paper_server(
        self,
        installer_jar: str,
        server_dir: str,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        """
        Install Paper by placing the downloaded server jar as paper-server.jar.

        Args:
            installer_jar: Path to the cached Paper server jar.
            server_dir: Directory to install the server into.
            progress_callback: Progress callback.

        Returns:
            (success, message) tuple.
        """
        import shutil

        Path(server_dir).mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback(0.5, _("Installing Paper server..."))

        try:
            source = Path(installer_jar)
            if not source.is_file() or source.stat().st_size < 1000:
                return False, _("Paper server jar is missing or corrupted")
            shutil.copyfile(source, Path(server_dir) / "paper-server.jar")
        except Exception as e:
            return False, _("Installation error: {}").format(e)

        if progress_callback:
            progress_callback(1.0, _("Paper server installed successfully"))
        return True, _("Installation successful")

    def _install_maven_loader_server(
        self,
        loader_name: str,
        java_path: str,
        installer_jar: str,
        mc_version: str,
        server_dir: str,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        """
        Run a Forge/NeoForge-style installer with --installServer to set up a server.

        Args:
            loader_name: Display name of the loader (for progress messages).
            java_path: Path to the java binary.
            installer_jar: Path to the loader installer JAR.
            mc_version: Minecraft version string.
            server_dir: Directory to install the server into.
            progress_callback: Progress callback.

        Returns:
            (success, message) tuple.
        """
        import subprocess

        Path(server_dir).mkdir(parents=True, exist_ok=True)

        cmd = [
            java_path,
            "-jar",
            installer_jar,
            "--installServer",
            server_dir,
        ]

        if progress_callback:
            progress_callback(0.5, _("Installing {} server for MC {}...").format(loader_name, mc_version))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
                cwd=server_dir,
                **hidden_subprocess_kwargs(),
            )

            if result.returncode == 0:
                # Verify install artifacts (run scripts and/or libraries dir)
                root = Path(server_dir)
                has_run_script = (root / "run.sh").exists() or (root / "run.bat").exists()
                has_libs = (root / "libraries").is_dir()
                if has_run_script or has_libs:
                    if progress_callback:
                        progress_callback(1.0, _("{} server installed successfully").format(loader_name))
                    return True, _("Installation successful")
                else:
                    return False, _("Installation completed but no run script was found")
            else:
                error_msg = result.stderr or result.stdout or _("Unknown error")
                return False, _("Installation failed: {}").format(error_msg)

        except subprocess.TimeoutExpired:
            return False, _("Installation timed out (15 minutes)")
        except Exception as e:
            return False, _("Installation error: {}").format(e)

    def install_fabric_server(
        self,
        java_path: str,
        installer_jar: str,
        mc_version: str,
        server_dir: str,
        loader_version: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
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

        # Validate installer file before invoking java
        installer_path = Path(installer_jar)
        if not installer_path.is_file():
            return False, _("Installer file not found at {} — please retry creation").format(installer_jar)
        if not _is_valid_jar(installer_path):
            try:
                installer_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False, _("Installer file is invalid or corrupted (not a JAR); please retry creation")

        Path(server_dir).mkdir(parents=True, exist_ok=True)

        cmd = [
            java_path,
            "-jar",
            installer_jar,
            "server",
            "-mcversion",
            mc_version,
            "-dir",
            server_dir,
        ]

        if loader_version:
            cmd.extend(["-loader", loader_version])

        if progress_callback:
            progress_callback(0.5, _("Installing Fabric server for MC {}...").format(mc_version))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=server_dir,
                **hidden_subprocess_kwargs(),
            )

            if result.returncode == 0:
                # Verify the launch jar exists
                launch_jar = Path(server_dir) / "fabric-server-launch.jar"
                if launch_jar.exists():
                    if progress_callback:
                        progress_callback(1.0, _("Fabric server installed successfully"))
                    return True, _("Installation successful")
                else:
                    return False, _("Installation completed but fabric-server-launch.jar not found")
            else:
                error_msg = result.stderr or result.stdout or _("Unknown error")
                return False, _("Installation failed: {}").format(error_msg)

        except subprocess.TimeoutExpired:
            return False, _("Installation timed out (5 minutes)")
        except Exception as e:
            return False, _("Installation error: {}").format(e)

    def fetch_all_versions_async(self, callback: Callable[[list[str], list[str]], None]):
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
