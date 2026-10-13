"""Tests for scan_update_compatibility bucketing.

Regression test: a modpack with releases (but none for the target MC
version) must land in "incompatible", not "unknown" ("Could Not Check").
"""

import json
import types

from hosty.shared.backend import modrinth_client
from hosty.shared.backend.server_manager import ServerInfo, ServerManager


def _manager_with_pack(tmp_path, projects, mods=None):
    (tmp_path / ".hosty-modpacks.json").write_text(json.dumps({"installed_projects": projects}))
    if mods is not None:
        (tmp_path / ".hosty-mod-installs.json").write_text(json.dumps({"mods": mods}))
    mgr = ServerManager.__new__(ServerManager)
    mgr._servers = {"s1": ServerInfo({"id": "s1", "path": str(tmp_path), "loader_type": "fabric"})}
    return mgr


def _version(mc_versions, loaders=("fabric",), number="2.0"):
    return types.SimpleNamespace(
        version_id="v-new",
        version_number=number,
        game_versions=list(mc_versions),
        loaders=list(loaders),
        filename="pack.mrpack",
        download_url="https://example.com/pack.mrpack",
    )


def _pack_entry():
    return {"version_id": "v-old", "version_number": "1.0", "title": "Pack", "mods": ["a.jar", "b.jar"]}


def test_supported_target_is_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(modrinth_client, "get_project_versions", lambda pid, **kw: [_version(["1.21.1"])])
    plan = _manager_with_pack(tmp_path, {"pid1": _pack_entry()}).scan_update_compatibility("s1", "1.21.1")
    assert [i["title"] for i in plan["compatible"]["modpacks"]] == ["Pack"]
    assert plan["unknown"]["modpacks"] == []
    assert plan["incompatible"]["modpacks"] == []


def test_unsupported_target_is_incompatible_not_unknown(tmp_path, monkeypatch):
    calls = []

    def fake(pid, game_versions=None, **kw):
        calls.append(game_versions)
        if game_versions:
            return []
        return [_version(["1.20.1"])]

    monkeypatch.setattr(modrinth_client, "get_project_versions", fake)
    plan = _manager_with_pack(tmp_path, {"pid1": _pack_entry()}).scan_update_compatibility("s1", "1.21.1")
    assert [i["title"] for i in plan["incompatible"]["modpacks"]] == ["Pack"]
    assert plan["unknown"]["modpacks"] == []
    assert calls == [["1.21.1"], None]


def test_unfiltered_retry_recovers_filter_miss(tmp_path, monkeypatch):
    def fake(pid, game_versions=None, **kw):
        return [] if game_versions else [_version(["1.21.1"])]

    monkeypatch.setattr(modrinth_client, "get_project_versions", fake)
    plan = _manager_with_pack(tmp_path, {"pid1": _pack_entry()}).scan_update_compatibility("s1", "1.21.1")
    assert [i["title"] for i in plan["compatible"]["modpacks"]] == ["Pack"]
    assert plan["unknown"]["modpacks"] == []


def test_total_failure_stays_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(modrinth_client, "get_project_versions", lambda pid, **kw: [])
    plan = _manager_with_pack(tmp_path, {"pid1": _pack_entry()}).scan_update_compatibility("s1", "1.21.1")
    assert [i["title"] for i in plan["unknown"]["modpacks"]] == ["Pack"]
    assert plan["incompatible"]["modpacks"] == []


def test_network_error_stays_unknown(tmp_path, monkeypatch):
    def fake(pid, **kw):
        raise TimeoutError("boom")

    monkeypatch.setattr(modrinth_client, "get_project_versions", fake)
    plan = _manager_with_pack(tmp_path, {"pid1": _pack_entry()}).scan_update_compatibility("s1", "1.21.1")
    assert [i["title"] for i in plan["unknown"]["modpacks"]] == ["Pack"]
    assert plan["incompatible"]["modpacks"] == []


def test_modpack_entries_carry_managed_mods(tmp_path, monkeypatch):
    """Every modpack plan entry exposes its managed files for the review UI."""

    def fake(pid, game_versions=None, **kw):
        if game_versions == ["1.21.1"]:
            return [_version(["1.21.1"])]
        if game_versions == ["2.0"]:
            return []
        return [_version(["1.20.1"])]

    monkeypatch.setattr(modrinth_client, "get_project_versions", fake)
    mgr = _manager_with_pack(tmp_path, {"pid1": _pack_entry()})

    plan = mgr.scan_update_compatibility("s1", "1.21.1")
    assert plan["compatible"]["modpacks"][0]["mods"] == ["a.jar", "b.jar"]

    plan = mgr.scan_update_compatibility("s1", "2.0")
    assert plan["incompatible"]["modpacks"][0]["mods"] == ["a.jar", "b.jar"]

    monkeypatch.setattr(modrinth_client, "get_project_versions", lambda pid, **kw: [])
    plan = mgr.scan_update_compatibility("s1", "1.21.1")
    assert plan["unknown"]["modpacks"][0]["mods"] == ["a.jar", "b.jar"]


def test_modpack_rows_carry_version_not_file_lists(tmp_path, monkeypatch):
    """Review subtitles use the version; the file list stays out of display."""

    def fake(pid, game_versions=None, **kw):
        if game_versions == ["1.21.1"]:
            return [_version(["1.21.1"], number="2.0")]
        if game_versions == ["2.0"]:
            return []
        return [_version(["1.20.1"], number="1.5")]

    monkeypatch.setattr(modrinth_client, "get_project_versions", fake)
    mgr = _manager_with_pack(tmp_path, {"pid1": _pack_entry()})

    plan = mgr.scan_update_compatibility("s1", "1.21.1")
    (entry,) = plan["compatible"]["modpacks"]
    assert entry["version_number"] == "2.0"

    plan = mgr.scan_update_compatibility("s1", "2.0")
    (entry,) = plan["incompatible"]["modpacks"]
    assert entry["version_number"] == "1.0"
    assert entry["version_id"] == "v-old"
    assert "a.jar" not in entry.get("version_number", "")

    monkeypatch.setattr(modrinth_client, "get_project_versions", lambda pid, **kw: [])
    plan = mgr.scan_update_compatibility("s1", "1.21.1")
    (entry,) = plan["unknown"]["modpacks"]
    assert entry["version_number"] == "1.0"


def test_incompatible_mod_row_carries_current_version(tmp_path, monkeypatch):
    """Incompatible/unknown mod rows also expose the installed version."""

    def fake(pid, game_versions=None, **kw):
        return [] if game_versions else [_version(["1.20.1"], number="9.9")]

    monkeypatch.setattr(modrinth_client, "get_project_versions", fake)
    meta = {"title": "Mod", "version_id": "v-old", "version_number": "0.11", "filename": "mod-old.jar"}
    mgr = _manager_with_pack(tmp_path, {}, mods={"mid": meta})
    plan = mgr.scan_update_compatibility("s1", "1.21.1")
    (entry,) = plan["incompatible"]["mods"]
    assert entry["version_number"] == "0.11"
    assert entry["filename"] == "mod-old.jar"

    monkeypatch.setattr(modrinth_client, "get_project_versions", lambda pid, **kw: [])
    plan = mgr.scan_update_compatibility("s1", "1.21.1")
    (entry,) = plan["unknown"]["mods"]
    assert entry["version_number"] == "0.11"


def test_mod_file_metadata(tmp_path):
    """Tracked mods resolve by filename to title/version/project for dialogs."""
    (tmp_path / ".hosty-mod-installs.json").write_text(
        json.dumps(
            {
                "mods": {
                    "pid-a": {
                        "title": "Fabric API",
                        "version_id": "abc123def456",
                        "version_number": "0.160.0",
                        "filename": "fabric-api-0.160.0.jar",
                    },
                    "pid-b": {
                        "title": "",
                        "version_id": "xyz987654321",
                        "version_number": "",
                        "filename": "orphan.jar",
                    },
                    "pid-c": {
                        "title": "NoFile",
                        "version_id": "v",
                        "version_number": "1.0",
                        "filename": "",
                    },
                }
            }
        )
    )
    mgr = ServerManager.__new__(ServerManager)
    mgr._servers = {"s1": ServerInfo({"id": "s1", "path": str(tmp_path), "loader_type": "fabric"})}

    meta = mgr.mod_file_metadata("s1")
    assert meta["fabric-api-0.160.0.jar"] == {
        "title": "Fabric API",
        "version": "0.160.0",
        "project_id": "pid-a",
    }
    assert meta["orphan.jar"]["title"] == "orphan.jar"
    assert meta["orphan.jar"]["version"] == "xyz98765"
    assert "" not in meta
    assert mgr.mod_file_metadata("missing") == {}


def _manager_at(tmp_path):
    mgr = ServerManager.__new__(ServerManager)
    mgr._servers = {"s1": ServerInfo({"id": "s1", "path": str(tmp_path), "loader_type": "fabric"})}
    return mgr


def test_modpack_file_details_normalizes(tmp_path):
    (tmp_path / ".hosty-modpacks.json").write_text(
        json.dumps(
            {
                "installed_projects": {
                    "pid1": {
                        "version_id": "v",
                        "version_number": "1.0",
                        "title": "Pack",
                        "mods": ["a.jar"],
                        "file_details": {
                            "A.JAR": {
                                "project_id": "proj-a",
                                "version_id": "ver-a",
                                "version_number": "2.0",
                                "title": "Mod A",
                            },
                            "": {"title": "Empty"},
                            "broken.jar": "not-a-dict",
                        },
                    }
                }
            }
        )
    )
    out = _manager_at(tmp_path).modpack_file_details("s1", "pid1")
    assert out == {"a.jar": {"title": "Mod A", "version": "2.0", "project_id": "proj-a"}}
    assert _manager_at(tmp_path).modpack_file_details("s1", "missing") == {}
    assert _manager_at(tmp_path).modpack_file_details("missing", "pid1") == {}


def test_backfill_modpack_file_details(tmp_path, monkeypatch):
    import hashlib

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    payload = b"jar-bytes-here"
    (mods_dir / "A.JAR").write_bytes(payload)
    digest = hashlib.sha512(payload).hexdigest()
    (tmp_path / ".hosty-modpacks.json").write_text(
        json.dumps(
            {
                "installed_projects": {
                    "pid1": {
                        "version_id": "v",
                        "version_number": "1.0",
                        "title": "Pack",
                        "mods": ["a.jar", "gone.jar"],
                    }
                }
            }
        )
    )
    mgr = _manager_at(tmp_path)

    seen = {}

    def fake_resolve(targets):
        seen["targets"] = list(targets)
        return {"a.jar": {"project_id": "p", "version_id": "v1", "version_number": "2.0", "title": "Mod"}}

    monkeypatch.setattr(modrinth_client, "resolve_mod_file_details", fake_resolve)

    assert mgr.backfill_modpack_file_details("s1") is True
    assert seen["targets"] == [("a.jar", digest)]
    stored = json.loads((tmp_path / ".hosty-modpacks.json").read_text())["installed_projects"]["pid1"]
    assert stored["file_details"]["a.jar"]["title"] == "Mod"
    assert mgr.backfill_modpack_file_details("s1") is False
    assert mgr.backfill_modpack_file_details("missing") is False
