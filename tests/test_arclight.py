"""Tests for Arclight (Bukkit + mods hybrid) server type support."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import hosty.shared.utils.constants as c
from hosty.shared.backend.download_manager import DownloadManager
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.backend.server_process import ServerProcess


def _manager_with_server(tmp_path: Path, **server_kwargs):
    mgr = ServerManager.__new__(ServerManager)
    data = {"id": "s1", "path": str(tmp_path), "loader_type": "arclight"}
    data.update(server_kwargs)
    mgr._servers = {"s1": ServerInfo(data)}
    mgr._listeners = {}
    return mgr


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_arclight_loader_registered():
    assert c.normalize_loader_type("Arclight") == "arclight"
    assert c.LOADER_ARCLIGHT in c.SUPPORTED_LOADERS
    assert c.mod_loader_name("arclight") == "Arclight"
    # Primary content dir stays mods/ for backward compatibility
    assert c.content_dir_name("arclight") == "mods"


def test_normalize_arclight_platform():
    assert c.normalize_arclight_platform("forge") == "forge"
    assert c.normalize_arclight_platform("Fabric") == "fabric"
    assert c.normalize_arclight_platform("NEOFORGE") == "neoforge"
    assert c.normalize_arclight_platform("") == "neoforge"
    assert c.normalize_arclight_platform(None) == "neoforge"
    assert c.normalize_arclight_platform("quilt") == "neoforge"


def test_content_dir_names():
    assert c.content_dir_names("arclight") == ["mods", "plugins"]
    assert c.content_dir_names("paper") == ["plugins"]
    assert c.content_dir_names("fabric") == ["mods"]
    assert c.content_dir_names("forge") == ["mods"]
    assert c.content_dir_names("neoforge") == ["mods"]
    assert c.content_dir_names(None) == ["mods"]


def test_supports_mods_plugins():
    assert c.supports_mods("arclight") is True
    assert c.supports_plugins("arclight") is True
    assert c.supports_mods("paper") is False
    assert c.supports_plugins("paper") is True
    assert c.supports_mods("fabric") is True
    assert c.supports_plugins("fabric") is False


def test_effective_mod_loader():
    assert c.effective_mod_loader("arclight", "forge") == "forge"
    assert c.effective_mod_loader("arclight", "") == "neoforge"
    assert c.effective_mod_loader("arclight", None) == "neoforge"
    assert c.effective_mod_loader("fabric", "forge") == "fabric"
    assert c.effective_mod_loader("paper", "") == "paper"


def test_arclight_api_paths():
    urls = c.arclight_api_urls("arclight/minecraft")
    assert len(urls) == 2
    assert all(u.endswith("/arclight/minecraft") for u in urls)
    assert "1.21.1" in c.arclight_loaders_path("1.21.1")
    assert "neoforge" in c.arclight_builds_path("1.21.1", "neoforge")


# ---------------------------------------------------------------------------
# DownloadManager (mocked network)
# ---------------------------------------------------------------------------


def _mock_resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.raise_for_status = MagicMock()
    return m


def _arclight_api_router(routes: dict):
    """Build a requests.get side effect routing Arclight API paths to JSON."""

    def fake_get(url, **kwargs):
        for path, payload in routes.items():
            if path in url:
                return _mock_resp(payload)
        raise AssertionError(f"unexpected url {url}")

    return fake_get


def test_fetch_arclight_mc_versions_sorted():
    dm = DownloadManager()
    payload = {"files": [{"name": "1.16.5"}, {"name": "1.21.1"}, {"name": "1.20.1"}]}
    with patch(
        "hosty.shared.backend.download_manager.requests.get",
        side_effect=_arclight_api_router({"arclight/minecraft": payload}),
    ):
        assert dm.fetch_arclight_mc_versions() == ["1.21.1", "1.20.1", "1.16.5"]


def test_fetch_arclight_platforms_preference_order():
    dm = DownloadManager()
    payload = {"files": [{"name": "fabric"}, {"name": "forge"}, {"name": "neoforge"}]}
    with patch(
        "hosty.shared.backend.download_manager.requests.get",
        side_effect=_arclight_api_router({"loaders": payload}),
    ):
        # Preference order, not API order
        assert dm.fetch_arclight_platforms("1.21.1") == ["neoforge", "forge", "fabric"]


def test_fetch_arclight_builds_sorted_newest_first():
    dm = DownloadManager()
    payload = {
        "files": [
            {
                "name": "1.0.1-8ec9529",
                "permlink": "https://dl/stable.jar",
                "link": "https://dl/stable-link",
                "last-modified": "2025-07-16T16:26:53.121Z",
            },
            {
                "name": "1.0.2-SNAPSHOT-d27101f",
                "permlink": "https://dl/snap.jar",
                "link": "https://dl/snap-link",
                "last-modified": "2026-09-15T12:45:07.236Z",
            },
        ]
    }
    with patch(
        "hosty.shared.backend.download_manager.requests.get",
        side_effect=_arclight_api_router({"versions-snapshot": payload}),
    ):
        builds = dm.fetch_arclight_builds("1.21.1", "neoforge")
        assert [b["name"] for b in builds] == ["1.0.2-SNAPSHOT-d27101f", "1.0.1-8ec9529"]
        assert builds[0]["permlink"] == "https://dl/snap.jar"
        assert builds[0]["stable"] is False
        assert builds[1]["stable"] is True


def test_resolve_arclight_returns_newest(monkeypatch):
    dm = DownloadManager()
    monkeypatch.setattr(
        dm,
        "fetch_arclight_builds",
        lambda mc, plat=None: [
            {"name": "1.0.2-SNAPSHOT-new", "permlink": "u1"},
            {"name": "1.0.1-old", "permlink": "u2"},
        ],
    )
    assert dm.resolve_loader_build("arclight", "1.21.1", platform="neoforge") == "1.0.2-SNAPSHOT-new"


def test_fetch_loader_builds_arclight_passes_platform(monkeypatch):
    dm = DownloadManager()
    seen = {}

    def fake_builds(mc, platform=None):
        seen["platform"] = platform
        return [{"name": "b1", "permlink": "u", "link": "", "last_modified": "", "stable": False}]

    monkeypatch.setattr(dm, "fetch_arclight_builds", fake_builds)
    assert dm.fetch_loader_builds("arclight", "1.21.1", "forge") == ["b1"]
    assert seen["platform"] == "forge"


def test_arclight_api_falls_back_to_second_base():
    dm = DownloadManager()
    payload = {"files": [{"name": "1.21.1"}]}
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "hypoglycemia" in url:
            raise ConnectionError("down")
        return _mock_resp(payload)

    with patch("hosty.shared.backend.download_manager.requests.get", side_effect=fake_get):
        assert dm.fetch_arclight_mc_versions() == ["1.21.1"]
    assert any("hypoglycemia" in u for u in calls)
    assert any("hypertention" in u for u in calls)


def test_download_installer_arclight_uses_permlink(tmp_path, monkeypatch):
    dm = DownloadManager()
    monkeypatch.setattr(
        dm,
        "fetch_arclight_builds",
        lambda mc, plat=None: [{"name": "1.0.2-SNAPSHOT-abc", "permlink": "https://dl/a.jar", "link": ""}],
    )
    jar_bytes = b"PK" + b"\x00" * (60 * 1024)

    def fake_get(url, **kwargs):
        assert url == "https://dl/a.jar"
        m = MagicMock()
        m.headers = {"content-length": str(len(jar_bytes))}
        m.iter_content = lambda chunk_size: [jar_bytes]
        m.raise_for_status = MagicMock()
        return m

    with (
        patch("hosty.shared.backend.download_manager.requests.get", side_effect=fake_get),
        patch("hosty.shared.backend.download_manager.CACHE_DIR", tmp_path),
    ):
        out = dm.download_installer(
            loader_type="arclight", mc_version="1.21.1", loader_version="1.0.2-SNAPSHOT-abc", platform="forge"
        )
    assert out is not None
    assert "arclight-1.21.1-forge" in out
    assert Path(out).read_bytes() == jar_bytes


def test_install_arclight_copies_jar(tmp_path):
    dm = DownloadManager()
    cached = tmp_path / "cached.jar"
    cached.write_bytes(b"PK" + b"\x00" * 2048)
    server_dir = tmp_path / "srv"
    ok, _msg = dm._install_arclight_server(str(cached), str(server_dir))
    assert ok is True
    assert (server_dir / "arclight-server.jar").exists()


def test_install_arclight_rejects_missing_jar(tmp_path):
    dm = DownloadManager()
    ok, _msg = dm._install_arclight_server(str(tmp_path / "nope.jar"), str(tmp_path / "srv"))
    assert ok is False


def test_install_server_dispatches_arclight(tmp_path):
    dm = DownloadManager()
    cached = tmp_path / "cached.jar"
    cached.write_bytes(b"PK" + b"\x00" * 2048)
    ok, _msg = dm.install_server(
        loader_type="arclight",
        java_path="java",
        installer_jar=str(cached),
        mc_version="1.21.1",
        server_dir=str(tmp_path / "srv"),
        loader_version="1.0.2-SNAPSHOT-abc",
    )
    assert ok is True
    assert (tmp_path / "srv" / "arclight-server.jar").exists()


# ---------------------------------------------------------------------------
# ServerProcess launch
# ---------------------------------------------------------------------------


def test_arclight_launch(tmp_path):
    p = ServerProcess(server_dir=str(tmp_path), java_path="/usr/bin/java", ram_mb=2048)
    (tmp_path / "arclight-server.jar").write_text("x")
    args, err = p._build_launch_command()
    assert err == ""
    assert args == ["-jar", "arclight-server.jar", "nogui"]


def test_missing_launch_config_mentions_arclight(tmp_path):
    p = ServerProcess(server_dir=str(tmp_path), java_path="/usr/bin/java", ram_mb=2048)
    args, err = p._build_launch_command()
    assert args is None
    assert "arclight-server.jar" in err


# ---------------------------------------------------------------------------
# ServerManager: platform field + version ordering
# ---------------------------------------------------------------------------


def test_add_server_stores_arclight_platform(server_manager):
    info = server_manager.add_server(
        name="Arc",
        mc_version="1.21.1",
        loader_version="1.0.2-SNAPSHOT-x",
        loader_type="arclight",
        arclight_platform="forge",
    )
    assert info.loader_type == "arclight"
    assert info.arclight_platform == "forge"
    fetched = server_manager.get_server(info.id)
    assert fetched is not None
    assert fetched.arclight_platform == "forge"
    assert fetched.to_dict()["arclight_platform"] == "forge"


def test_add_server_ignores_platform_for_other_loaders(server_manager):
    info = server_manager.add_server(
        name="Fab",
        mc_version="1.21.4",
        loader_version="0.16.9",
        loader_type="fabric",
        arclight_platform="forge",
    )
    assert info.arclight_platform == ""


def test_is_loader_version_newer_arclight():
    assert ServerManager.is_loader_version_newer("b", "a", "arclight") is True
    assert ServerManager.is_loader_version_newer("same", "same", "arclight") is False
    assert ServerManager.is_loader_version_newer("x", "", "arclight") is True
    assert ServerManager.is_loader_version_newer("", "x", "arclight") is False
    # Other loaders keep ordered semantics
    assert ServerManager.is_loader_version_newer("0.15.0", "0.16.9", "fabric") is False


def test_filter_upgrade_builds_arclight_positional():
    builds = ["snap-new", "snap-mid", "snap-old"]
    # Newest-first listing: only builds ahead of the installed one are upgrades
    assert ServerManager.filter_upgrade_builds(builds, "snap-old", "arclight") == ["snap-new", "snap-mid"]
    assert ServerManager.filter_upgrade_builds(builds, "snap-mid", "arclight") == ["snap-new"]
    assert ServerManager.filter_upgrade_builds(builds, "snap-new", "arclight") == []
    # Installed build pruned from the listing: allow all
    assert ServerManager.filter_upgrade_builds(builds, "snap-gone", "arclight") == builds
    assert ServerManager.filter_upgrade_builds(builds, "", "arclight") == builds


def test_filter_upgrade_builds_other_loaders_unchanged():
    assert ServerManager.filter_upgrade_builds(["0.16.9", "0.15.0"], "0.15.0", "fabric") == ["0.16.9"]
    assert ServerManager.filter_upgrade_builds(["0.16.9", "0.15.0"], "0.16.9", "fabric") == []


# ---------------------------------------------------------------------------
# ServerManager: plugin tracking (mocked Modrinth)
# ---------------------------------------------------------------------------


def _plugin_version(version_id="v2", filename="CoolPlugin-2.0.jar", game_versions=None, loaders=None):
    from hosty.shared.backend.modrinth_client import ModrinthVersion

    return ModrinthVersion(
        version_id=version_id,
        project_id="coolplugin",
        title="CoolPlugin",
        name="2.0",
        version_number="2.0",
        game_versions=list(game_versions or ["1.21.1"]),
        loaders=list(loaders if loaders is not None else ["paper"]),
        published="2026-01-01",
        download_url=f"https://dl/{filename}",
        filename=filename,
        hashes={},
    )


def _write_plugin_state(root: Path, plugins: dict):
    (root / ".hosty-plugin-installs.json").write_text(json.dumps({"plugins": plugins}))


def test_scan_compatibility_plugins(tmp_path):
    mgr = _manager_with_server(tmp_path, mc_version="1.21.1", arclight_platform="neoforge")
    _write_plugin_state(
        tmp_path,
        {
            "coolplugin": {
                "title": "CoolPlugin",
                "version_id": "v1",
                "version_number": "1.0",
                "filename": "CoolPlugin-1.0.jar",
            }
        },
    )
    new = _plugin_version()
    with patch("hosty.shared.backend.modrinth_client.get_project_versions", return_value=[new]):
        plan = mgr.scan_update_compatibility("s1", "1.21.1")
    assert [e["project_id"] for e in plan["compatible"]["plugins"]] == ["coolplugin"]
    assert plan["incompatible"]["plugins"] == []


def test_scan_compatibility_plugin_incompatible(tmp_path):
    mgr = _manager_with_server(tmp_path, mc_version="1.21.1", arclight_platform="neoforge")
    _write_plugin_state(
        tmp_path,
        {
            "coolplugin": {
                "title": "CoolPlugin",
                "version_id": "v1",
                "version_number": "1.0",
                "filename": "CoolPlugin-1.0.jar",
            }
        },
    )
    old_only = _plugin_version(game_versions=["1.20.1"])
    with patch("hosty.shared.backend.modrinth_client.get_project_versions", return_value=[old_only]):
        plan = mgr.scan_update_compatibility("s1", "1.21.1")
    assert plan["compatible"]["plugins"] == []
    assert [e["project_id"] for e in plan["incompatible"]["plugins"]] == ["coolplugin"]


def test_apply_and_isolate_plugin(tmp_path):
    mgr = _manager_with_server(tmp_path, mc_version="1.21.1", arclight_platform="neoforge")
    _write_plugin_state(
        tmp_path,
        {
            "coolplugin": {
                "title": "CoolPlugin",
                "version_id": "v1",
                "version_number": "1.0",
                "filename": "CoolPlugin-1.0.jar",
            }
        },
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "CoolPlugin-1.0.jar").write_text("old")

    new = _plugin_version()
    entry = {
        "title": "CoolPlugin",
        "project_id": "coolplugin",
        "current_filename": "CoolPlugin-1.0.jar",
        "filename": new.filename,
        "version_id": new.version_id,
        "version_number": new.version_number,
        "download_url": new.download_url,
    }
    with patch(
        "hosty.shared.backend.modrinth_client.download_to",
        side_effect=lambda url, dest, **kw: Path(dest).write_text("new"),
    ):
        applied, failed = mgr.apply_compatible_component_updates(
            "s1", "1.21.1", plan={"compatible": {"plugins": [entry]}, "incompatible": {}}
        )
    assert (applied, failed) == (1, 0)
    assert (tmp_path / "plugins" / new.filename).exists()
    assert not (tmp_path / "plugins" / "CoolPlugin-1.0.jar").exists()

    # Now isolate an incompatible plugin
    _write_plugin_state(
        tmp_path,
        {"coolplugin": {"title": "CoolPlugin", "version_id": "v2", "version_number": "2.0", "filename": new.filename}},
    )
    bad = dict(entry)
    bad["reason"] = "No Modrinth plugin release for Minecraft 1.21.1"
    record = mgr.isolate_incompatible_components("s1", "1.21.1", plan={"incompatible": {"plugins": [bad]}})
    assert [e["project_id"] for e in record["plugins"]] == ["coolplugin"]
    assert (tmp_path / "plugins_incompatible" / new.filename).exists()
    assert mgr.get_incompatible_components("s1")["plugins"] != []


def test_retry_restores_fileless_plugin(tmp_path):
    mgr = _manager_with_server(tmp_path, mc_version="1.21.1", arclight_platform="neoforge")
    mgr._write_json_file(
        tmp_path / ".hosty-incompatible-components.json",
        {"mods": [], "modpacks": [], "datapacks": [], "plugins": [{"project_id": "coolplugin", "title": "CoolPlugin"}]},
    )
    new = _plugin_version()
    with (
        patch("hosty.shared.backend.modrinth_client.find_compatible_plugin_version", return_value=new),
        patch(
            "hosty.shared.backend.modrinth_client.download_to",
            side_effect=lambda url, dest, **kw: Path(dest).write_text("x"),
        ),
    ):
        restored, failed = mgr.retry_incompatible_components("s1")
    assert (restored, failed) == (1, 0)
    assert (tmp_path / "plugins" / new.filename).exists()
    assert mgr.get_incompatible_components("s1")["plugins"] == []


def test_delete_incompatible_plugin(tmp_path):
    mgr = _manager_with_server(tmp_path, mc_version="1.21.1")
    disabled = tmp_path / "plugins_incompatible"
    disabled.mkdir()
    (disabled / "OldPlugin.jar").write_text("x")
    mgr._write_json_file(
        tmp_path / ".hosty-incompatible-components.json",
        {
            "mods": [],
            "modpacks": [],
            "datapacks": [],
            "plugins": [{"project_id": "oldplugin", "title": "OldPlugin", "filename": "OldPlugin.jar"}],
        },
    )
    ok, _msg = mgr.delete_incompatible_component("s1", "plugin", project_id="oldplugin")
    assert ok is True
    assert not (disabled / "OldPlugin.jar").exists()
    assert mgr.get_incompatible_components("s1")["plugins"] == []


def test_get_incompatible_components_legacy_json(tmp_path):
    mgr = _manager_with_server(tmp_path, mc_version="1.21.1")
    (tmp_path / ".hosty-incompatible-components.json").write_text(json.dumps({"mods": []}))
    data = mgr.get_incompatible_components("s1")
    assert data["plugins"] == []


# ---------------------------------------------------------------------------
# modrinth_client plugin support
# ---------------------------------------------------------------------------


def test_search_mods_plugin_type():
    from hosty.shared.backend import modrinth_client

    captured = {}

    def fake_request_json(url, timeout=30.0):
        captured["url"] = url
        return {"hits": [], "total_hits": 0}

    with patch.object(modrinth_client, "_request_json", side_effect=fake_request_json):
        hits, total = modrinth_client.search_mods("essentials", project_type="plugin")
    assert (hits, total) == ([], 0)
    assert "project_type%3Aplugin" in captured["url"] or "project_type:plugin" in captured["url"]
    assert "categories%3A" not in captured["url"] and "categories:" not in captured["url"]


def test_find_compatible_plugin_version_prefers_bukkit_loader():
    from hosty.shared.backend import modrinth_client

    spigot_only = _plugin_version(version_id="v-spigot", filename="P-spigot.jar", loaders=["spigot"])
    fabric_mod = _plugin_version(version_id="v-fabric", filename="P-fabric.jar", loaders=["fabric"])
    with patch.object(modrinth_client, "get_project_versions", return_value=[fabric_mod, spigot_only]):
        chosen = modrinth_client.find_compatible_plugin_version("someplugin", "1.21.1")
    assert chosen is not None
    assert chosen.version_id == "v-spigot"


# ---------------------------------------------------------------------------
# playit_manager hybrid paths
# ---------------------------------------------------------------------------


def test_content_jar_dirs():
    from hosty.shared.backend.playit_manager import PlayitManager

    assert PlayitManager._content_jar_dirs("/srv", "arclight") == [
        Path("/srv/mods"),
        Path("/srv/plugins"),
    ]
    assert PlayitManager._content_jar_dirs("/srv", "paper") == [Path("/srv/plugins")]
    assert PlayitManager._content_jar_dirs("/srv", "fabric") == [Path("/srv/mods")]


def test_geyser_config_dir_arclight(tmp_path):
    from hosty.shared.backend.playit_manager import PlayitManager

    root = str(tmp_path)
    # Default: platform mod config
    assert PlayitManager._geyser_config_dir(root, "arclight", "forge") == (tmp_path / "config" / "Geyser-Forge")
    assert PlayitManager._geyser_config_dir(root, "arclight", "") == (tmp_path / "config" / "Geyser-NeoForge")
    # Existing plugin config wins (manually installed Bukkit variant)
    plugin_cfg = tmp_path / "plugins" / "Geyser-Spigot"
    plugin_cfg.mkdir(parents=True)
    (plugin_cfg / "config.yml").write_text("x")
    assert PlayitManager._geyser_config_dir(root, "arclight", "forge") == plugin_cfg
    # Non-arclight behavior unchanged
    assert PlayitManager._geyser_config_dir(root, "paper") == (tmp_path / "plugins" / "Geyser-Spigot")
    assert PlayitManager._geyser_config_dir(root, "fabric") == (tmp_path / "config" / "Geyser-Fabric")


def test_jar_present_scans_both_arclight_dirs(tmp_path):
    from hosty.shared.backend.playit_manager import PlayitManager

    (tmp_path / "mods").mkdir()
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Geyser-Spigot.jar").write_text("x")
    dirs = PlayitManager._content_jar_dirs(str(tmp_path), "arclight")
    assert PlayitManager._jar_present(dirs, "geyser") is True
    assert PlayitManager._jar_present(dirs, "floodgate") is False


# ---------------------------------------------------------------------------
# Live smoke tests - hit real APIs, skipped if offline
# ---------------------------------------------------------------------------


@pytest.mark.network
def test_live_arclight_mc_versions():
    dm = DownloadManager()
    versions = dm.fetch_arclight_mc_versions()
    assert "1.21.1" in versions
    assert "1.16.5" in versions


@pytest.mark.network
def test_live_arclight_platforms_and_build():
    dm = DownloadManager()
    assert dm.fetch_arclight_platforms("1.21.1") == ["neoforge", "forge", "fabric"]
    assert dm.fetch_arclight_platforms("1.20.1") == ["forge"]
    build = dm.resolve_loader_build("arclight", "1.21.1", platform="neoforge")
    assert build
    assert dm._arclight_download_url("1.21.1", "neoforge", build).startswith("https://")
