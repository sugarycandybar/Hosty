"""Unit tests for loader / java-version helpers."""

import hosty.shared.utils.constants as c
from hosty.shared.backend.download_manager import DownloadManager


def test_normalize_loader_type():
    assert c.normalize_loader_type("fabric") == "fabric"
    assert c.normalize_loader_type("Paper") == "paper"
    assert c.normalize_loader_type("NEOFORGE") == "neoforge"
    assert c.normalize_loader_type("forge") == "forge"
    assert c.normalize_loader_type("quilt") == "fabric"  # unknown -> fabric
    assert c.normalize_loader_type(None) == "fabric"
    assert c.normalize_loader_type("") == "fabric"


def test_content_dir_name():
    assert c.content_dir_name("fabric") == "mods"
    assert c.content_dir_name("forge") == "mods"
    assert c.content_dir_name("neoforge") == "mods"
    assert c.content_dir_name("paper") == "plugins"
    assert c.content_dir_name(None) == "mods"
    assert c.content_dir_name("quilt") == "mods"


def test_mod_loader_name():
    assert c.mod_loader_name("paper") == "Paper"
    assert c.mod_loader_name("folia") == "Folia"
    assert c.mod_loader_name("fabric") == "Fabric"


def test_folia_loader_registered():
    assert c.normalize_loader_type("Folia") == "folia"
    assert c.LOADER_FOLIA in c.SUPPORTED_LOADERS
    assert c.content_dir_name("folia") == "plugins"
    assert c.content_dir_names("folia") == ["plugins"]
    assert c.supports_mods("folia") is False
    assert c.supports_plugins("folia") is True
    assert c.server_jar_filename("folia") == "folia-server.jar"
    assert c.papermc_project("folia") == "folia"
    assert c.is_papermc_loader("paper") is True
    assert c.is_papermc_loader("folia") is True
    assert c.is_papermc_loader("fabric") is False


def test_get_required_java_version():
    assert c.get_required_java_version("1.16.5") == 8
    assert c.get_required_java_version("1.17") == 16
    assert c.get_required_java_version("1.18.2") == 17
    assert c.get_required_java_version("1.20.1") == 17
    assert c.get_required_java_version("1.20.5") == 21
    assert c.get_required_java_version("1.21.4") == 21
    assert c.get_required_java_version("26.1") == 25
    assert c.get_required_java_version("26.2") == 25
    assert c.get_required_java_version("") == c.DEFAULT_JAVA_VERSION


def test_neoforge_branch_segments():
    f = DownloadManager._neoforge_branch_segments
    assert f("1.21.4") == [21, 4]
    assert f("1.21") == [21, 0]
    assert f("26.2") == [26, 2]
    assert f("26.1.2") == [26, 1, 2]
    assert f("") == []
    assert f("26.2-rc1") == []
    assert f("abc") == []


def test_neoforge_branch_prefix_no_longer_exists():
    # Old helper was removed - ensure new one is used
    assert not hasattr(DownloadManager, "_neoforge_branch_prefix")
