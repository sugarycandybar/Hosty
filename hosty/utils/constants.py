"""
Central constants for Hosty application.
"""
import os
import sys
from pathlib import Path

# Application identity
APP_ID = "io.github.sugarycandybar.Hosty"
APP_NAME = "Hosty"
APP_VERSION = "1.2.2"
APP_WEBSITE = "https://github.com/sugarycandybar/Hosty"

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

# Ensure directories exist
for d in [DATA_DIR, SERVERS_DIR, JRES_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Fabric Meta API
FABRIC_META_BASE = "https://meta.fabricmc.net/v2/versions"
FABRIC_GAME_VERSIONS_URL = f"{FABRIC_META_BASE}/game"
FABRIC_LOADER_VERSIONS_URL = f"{FABRIC_META_BASE}/loader"
FABRIC_INSTALLER_VERSIONS_URL = f"{FABRIC_META_BASE}/installer"

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

    url = (
        f"{ADOPTIUM_API_BASE}/{java_version}/ga/"
        f"{os_name}/{arch}/{image_type}/hotspot/normal/eclipse"
    )
    return url, archive_type


def get_adoptium_jre_url(java_version: int) -> str:
    """Backward-compatible helper that returns only the Adoptium JRE URL."""
    return get_adoptium_jre_download_info(java_version)[0]

# Java version mapping: MC version prefix -> required Java version
JAVA_VERSION_MAP = [
    # Order matters: check from newest to oldest
    ("26.", 25),
    ("25.", 25),
    ("1.21", 21),
    ("1.20.5", 21),
    ("1.20.4", 17),
    ("1.20.3", 17),
    ("1.20.2", 17),
    ("1.20.1", 17),
    ("1.20", 17),
    ("1.19", 17),
    ("1.18", 17),
    ("1.17", 17),
    ("1.16", 11),
]
DEFAULT_JAVA_VERSION = 21

def get_required_java_version(mc_version: str) -> int:
    """Determine the required Java version for a Minecraft version."""
    for prefix, java_ver in JAVA_VERSION_MAP:
        if mc_version.startswith(prefix):
            return java_ver
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

# Common server commands
COMMON_COMMANDS = [
    {"label": "Stop Server", "command": "/stop", "needs_args": False},
    {"label": "Save All", "command": "/save-all", "needs_args": False},
    {"label": "List Players", "command": "/list", "needs_args": False},
    {"label": "Say Message...", "command": "/say ", "needs_args": True},

    {"label": "Op Player...", "command": "/op ", "needs_args": True},
    {"label": "Deop Player...", "command": "/deop ", "needs_args": True},
    {"label": "Kick Player...", "command": "/kick ", "needs_args": True},
    {"label": "Ban Player...", "command": "/ban ", "needs_args": True},
    {"label": "Pardon Player...", "command": "/pardon ", "needs_args": True},
    {"label": "Whitelist Add...", "command": "/whitelist add ", "needs_args": True},
    {"label": "Whitelist Remove...", "command": "/whitelist remove ", "needs_args": True},

    {"label": "Gamemode Survival...", "command": "/gamemode survival ", "needs_args": True},
    {"label": "Gamemode Creative...", "command": "/gamemode creative ", "needs_args": True},
    {"label": "Gamemode Spectator...", "command": "/gamemode spectator ", "needs_args": True},

    {"label": "Set Difficulty Peaceful", "command": "/difficulty peaceful", "needs_args": False},
    {"label": "Set Difficulty Easy", "command": "/difficulty easy", "needs_args": False},
    {"label": "Set Difficulty Normal", "command": "/difficulty normal", "needs_args": False},
    {"label": "Set Difficulty Hard", "command": "/difficulty hard", "needs_args": False},

    {"label": "Set Time Day", "command": "/time set day", "needs_args": False},
    {"label": "Set Time Night", "command": "/time set night", "needs_args": False},
    {"label": "Set Weather Clear", "command": "/weather clear", "needs_args": False},
    {"label": "Set Weather Rain", "command": "/weather rain", "needs_args": False},
    {"label": "Set Weather Thunder", "command": "/weather thunder", "needs_args": False},
    {"label": "Teleport Player...", "command": "/tp ", "needs_args": True},
    {"label": "Show Seed", "command": "/seed", "needs_args": False},
]

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
    "minecraft\\:normal": "Default",
    "minecraft\\:flat": "Flat",
    "minecraft\\:large_biomes": "Large Biomes",
    "minecraft\\:amplified": "Amplified",
    "minecraft\\:single_biome_surface": "Single Biome",
}

# Server status
class ServerStatus:
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"

# Default RAM allocation in MB
DEFAULT_RAM_MB = 2048
MIN_RAM_MB = 512
MAX_RAM_MB = 16384
