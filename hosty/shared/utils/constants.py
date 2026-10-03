"""
Central constants for Hosty application.
"""

import os
import re
import sys
from pathlib import Path

import hosty.i18n  # noqa: F401 - installs _() into builtins
from hosty.version import __version__

# Application identity
APP_ID = "io.github.sugarycandybar.Hosty"
APP_NAME = "Hosty"
APP_VERSION = __version__
APP_WEBSITE = "https://github.com/sugarycandybar/Hosty"

try:
    from hosty.build_info import BUILD_CHANNEL as APP_CHANNEL
    from hosty.build_info import BUILD_GIT_SHA as APP_BUILD_SHA
except Exception:
    APP_CHANNEL = "dev"
    APP_BUILD_SHA = "unknown"


def _display_version() -> str:
    if APP_CHANNEL == "beta" and APP_BUILD_SHA not in ("", "unknown"):
        return f"{APP_VERSION} (beta {APP_BUILD_SHA})"
    if APP_CHANNEL == "beta":
        return f"{APP_VERSION} (beta)"
    return APP_VERSION


APP_VERSION_DISPLAY = _display_version()

# Directories


def _default_data_dir() -> Path:
    """Return a sensible per-user data directory for the current platform."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Hosty"
        return Path.home() / "AppData" / "Local" / "Hosty"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Hosty"

    return Path.home() / ".local" / "share" / "hosty"


DATA_DIR = Path(os.environ.get("HOSTY_DATA_DIR", _default_data_dir()))
SERVERS_DIR = DATA_DIR / "servers"
JRES_DIR = DATA_DIR / "jres"
CACHE_DIR = DATA_DIR / "cache"
CONFIG_FILE = DATA_DIR / "servers.json"

# Fabric Meta API
FABRIC_META_BASE = "https://meta.fabricmc.net/v2/versions"
FABRIC_GAME_VERSIONS_URL = f"{FABRIC_META_BASE}/game"
FABRIC_LOADER_VERSIONS_URL = f"{FABRIC_META_BASE}/loader"
FABRIC_INSTALLER_VERSIONS_URL = f"{FABRIC_META_BASE}/installer"

# Forge / NeoForge APIs
FORGE_PROMOTIONS_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN_INSTALLER_URL = (
    "https://maven.minecraftforge.net/net/minecraftforge/forge/{mc}-{version}/forge-{mc}-{version}-installer.jar"
)
NEOFORGE_VERSIONS_URL = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
NEOFORGE_MAVEN_INSTALLER_URL = (
    "https://maven.neoforged.net/releases/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar"
)

# PaperMC Fill API (v2 is sunset)
PAPER_FILL_API_BASE = "https://fill.papermc.io/v3"
PAPER_LATEST_BUILD_URL = f"{PAPER_FILL_API_BASE}/projects/paper/versions/{{mc}}/builds/latest"
PAPER_BUILD_URL = f"{PAPER_FILL_API_BASE}/projects/paper/versions/{{mc}}/builds/{{build}}"
HOSTY_USER_AGENT = f"Hosty/{APP_VERSION} (+{APP_WEBSITE})"

# Arclight download API (official download site backend, see arclight.izzel.io).
# Virtual filesystem: /arclight/minecraft -> MC versions;
# /arclight/minecraft/{mc}/loaders -> base platforms (forge/fabric/neoforge);
# /arclight/minecraft/{mc}/loaders/{platform}/versions-snapshot -> Arclight builds
# (each entry carries a ``permlink`` direct-download URL and ``last-modified``).
ARCLIGHT_FILES_API_BASES = [
    "https://files.hypoglycemia.icu/v1/files",
    "https://files.hypertention.cn/v1/files",
]
ARCLIGHT_MINECRAFT_PATH = "arclight/minecraft"


def arclight_api_urls(path: str) -> list[str]:
    """Return candidate API URLs for an Arclight files path (primary + fallback)."""
    stripped = str(path or "").strip().strip("/")
    return [f"{base}/{stripped}" for base in ARCLIGHT_FILES_API_BASES]


def arclight_loaders_path(mc_version: str) -> str:
    """API path listing base platforms for an Arclight Minecraft version."""
    return f"{ARCLIGHT_MINECRAFT_PATH}/{str(mc_version or '').strip()}/loaders"


def arclight_builds_path(mc_version: str, platform: str) -> str:
    """API path listing Arclight builds for an MC version + base platform."""
    return f"{arclight_loaders_path(mc_version)}/{str(platform or '').strip().lower()}/versions-snapshot"


# Mod loaders
LOADER_FABRIC = "fabric"
LOADER_NEOFORGE = "neoforge"
LOADER_FORGE = "forge"
LOADER_PAPER = "paper"
LOADER_ARCLIGHT = "arclight"
SUPPORTED_LOADERS = [LOADER_FABRIC, LOADER_NEOFORGE, LOADER_FORGE, LOADER_PAPER, LOADER_ARCLIGHT]
LOADER_NAMES = {
    LOADER_FABRIC: _("Fabric"),
    LOADER_NEOFORGE: _("NeoForge"),
    LOADER_FORGE: _("Forge"),
    LOADER_PAPER: _("Paper"),
    LOADER_ARCLIGHT: _("Arclight"),
}

# Base mod platforms an Arclight build can target. Arclight is a Bukkit
# implementation on top of a mod loader, so each Minecraft version offers one
# or more of these variants (older MC versions are Forge-only).
ARCLIGHT_PLATFORMS = [LOADER_NEOFORGE, LOADER_FORGE, LOADER_FABRIC]
ARCLIGHT_DEFAULT_PLATFORM = LOADER_NEOFORGE


def normalize_arclight_platform(value: str | None) -> str:
    """Return a known Arclight base platform, defaulting to NeoForge."""
    value = str(value or "").strip().lower()
    if value in (LOADER_FORGE, LOADER_FABRIC, LOADER_NEOFORGE):
        return value
    return ARCLIGHT_DEFAULT_PLATFORM


def normalize_loader_type(value: str | None) -> str:
    """Return a known loader id, defaulting to fabric for unknown/legacy values."""
    value = str(value or "").strip().lower()
    return value if value in SUPPORTED_LOADERS else LOADER_FABRIC


def mod_loader_name(loader_type: str | None) -> str:
    """Return the display name for a loader type."""
    return LOADER_NAMES.get(normalize_loader_type(loader_type), LOADER_NAMES[LOADER_FABRIC])


def content_dir_name(loader_type: str | None) -> str:
    """Directory (inside the server dir) where loader jars live."""
    return "plugins" if normalize_loader_type(loader_type) == LOADER_PAPER else "mods"


def content_dir_names(loader_type: str | None) -> list[str]:
    """All content directories (inside the server dir) for a loader type.

    Arclight is a hybrid: mods live in ``mods/`` and Bukkit plugins in
    ``plugins/`` simultaneously. Paper only has ``plugins/``.
    """
    loader = normalize_loader_type(loader_type)
    if loader == LOADER_ARCLIGHT:
        return ["mods", "plugins"]
    if loader == LOADER_PAPER:
        return ["plugins"]
    return ["mods"]


def supports_mods(loader_type: str | None) -> bool:
    """True when the loader runs mods from ``mods/``."""
    return normalize_loader_type(loader_type) != LOADER_PAPER


def supports_plugins(loader_type: str | None) -> bool:
    """True when the loader runs Bukkit plugins from ``plugins/``."""
    return normalize_loader_type(loader_type) in (LOADER_PAPER, LOADER_ARCLIGHT)


def effective_mod_loader(loader_type: str | None, arclight_platform: str | None = None) -> str:
    """Modrinth loader id to use for *mod* queries/installs.

    Arclight mods target its underlying base platform (e.g. a NeoForge-based
    Arclight server runs NeoForge mods), so the platform is returned instead
    of ``arclight`` (which Modrinth does not know).
    """
    loader = normalize_loader_type(loader_type)
    if loader == LOADER_ARCLIGHT:
        return normalize_arclight_platform(arclight_platform)
    return loader


# Adoptium JRE API
ADOPTIUM_API_BASE = "https://api.adoptium.net/v3/binary/latest"


def get_adoptium_jre_download_info(java_version: int) -> tuple[str, str]:
    """
    Return a platform-specific Adoptium JRE download URL and archive type.

    Returns:
        (url, archive_type) where archive_type is "zip" or "tar.gz".
    """
    import platform

    machine = platform.machine()
    arch_map = {
        "x86_64": "x64",
        "AMD64": "x64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    arch = arch_map.get(machine, "x64")

    if sys.platform == "win32":
        os_name = "windows"
        image_type = "jre"
        archive_type = "zip"
    elif sys.platform == "darwin":
        os_name = "mac"
        image_type = "jre"
        archive_type = "tar.gz"
    else:
        os_name = "linux"
        image_type = "jre"
        archive_type = "tar.gz"

    url = f"{ADOPTIUM_API_BASE}/{java_version}/ga/{os_name}/{arch}/{image_type}/hotspot/normal/eclipse"
    return url, archive_type


def get_adoptium_jre_url(java_version: int) -> str:
    """Backward-compatible helper that returns only the Adoptium JRE URL."""
    return get_adoptium_jre_download_info(java_version)[0]


DEFAULT_JAVA_VERSION = 21


def _parse_mc_version_tuple(mc_version: str) -> tuple[int, int, int] | None:
    """Parse a Minecraft version string into (major, minor, patch)."""
    match = re.match(r"^(\d+(?:\.\d+){0,2})", mc_version or "")
    if not match:
        return None

    nums = [int(part) for part in match.group(1).split(".")]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def get_required_java_version(mc_version: str) -> int:
    """Determine required Java version for Minecraft version ranges."""
    parsed = _parse_mc_version_tuple(mc_version)
    if not parsed:
        return DEFAULT_JAVA_VERSION

    # Fabric requirements:
    # 26.1+ -> Java 25+
    # 1.20.5 - 1.21.11 -> Java 21+
    # 1.18 - 1.20.4 -> Java 17+
    # 1.17 - 1.17.1 -> Java 16+
    # 1.12 - 1.16.5 -> Java 8+
    if parsed >= (26, 1, 0):
        return 25
    if (1, 20, 5) <= parsed <= (1, 21, 11):
        return 21
    if (1, 18, 0) <= parsed <= (1, 20, 4):
        return 17
    if (1, 17, 0) <= parsed <= (1, 17, 1):
        return 16
    if (1, 12, 0) <= parsed <= (1, 16, 5):
        return 8

    return DEFAULT_JAVA_VERSION


# Default server.properties values
DEFAULT_SERVER_PROPERTIES = {
    "motd": "a hosty server",
    "max-players": "20",
    "difficulty": "easy",
    "gamemode": "survival",
    "pvp": "true",
    "online-mode": "true",
    "white-list": "false",
    "allow-flight": "false",
    "view-distance": "10",
    "simulation-distance": "10",
    "server-port": "25565",
    "level-seed": "",
    "level-type": "minecraft\\:normal",
    "spawn-protection": "16",
    "enable-command-block": "false",
    "allow-nether": "true",
    "hardcore": "false",
    "enable-rcon": "false",
    "max-world-size": "29999984",
    "enable-query": "false",
}


# Difficulty options
DIFFICULTIES = ["peaceful", "easy", "normal", "hard"]

# Gamemode options
GAMEMODES = ["survival", "creative", "adventure", "spectator"]

# Level types
LEVEL_TYPES = [
    "minecraft\\:normal",
    "minecraft\\:flat",
    "minecraft\\:large_biomes",
    "minecraft\\:amplified",
    "minecraft\\:single_biome_surface",
]

# Display names for level types
LEVEL_TYPE_NAMES = {
    "minecraft\\:normal": _("Default"),
    "minecraft\\:flat": _("Flat"),
    "minecraft\\:large_biomes": _("Large Biomes"),
    "minecraft\\:amplified": _("Amplified"),
    "minecraft\\:single_biome_surface": _("Single Biome"),
}


# Server status
class ServerStatus:
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


# Default RAM allocation in MB
MIN_RAM_MB = 512


def ensure_data_dirs() -> None:
    for d in [DATA_DIR, SERVERS_DIR, JRES_DIR, CACHE_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def get_system_ram_mb() -> int:
    """Return the total system RAM in Megabytes."""
    try:
        import psutil

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        if sys.platform == "win32":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                return int(stat.ullTotalPhys / (1024 * 1024))
            except Exception:
                pass
        else:
            try:
                return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
            except Exception:
                pass
    return 16384  # Default fallback if all fails (16GB)


def _get_max_ram_mb() -> int:
    sys_ram = get_system_ram_mb()
    # Determine OS headroom to leave system responsive:
    # <= 4GB system: leave 1GB
    # <= 8GB system: leave 1.5GB
    # > 8GB system: leave 2GB
    if sys_ram <= 4096:
        headroom = 1024
    elif sys_ram <= 8192:
        headroom = 1536
    else:
        headroom = 2048
    return max(MIN_RAM_MB, sys_ram - headroom)


MAX_RAM_MB = _get_max_ram_mb()
DEFAULT_RAM_MB = min(2048, MAX_RAM_MB)
