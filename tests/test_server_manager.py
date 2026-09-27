"""Tests for server start/stop coordination and Bedrock transport enforcement."""

import threading

from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.utils.constants import ServerStatus


def _manager_with_server(tmp_path, **server_kwargs):
    from hosty.shared.backend.playit_config import save_playit_config

    mgr = ServerManager.__new__(ServerManager)
    data = {"id": "s1", "path": str(tmp_path), "loader_type": "fabric"}
    data.update(server_kwargs)
    mgr._servers = {"s1": ServerInfo(data)}
    save_playit_config(tmp_path, dict(server_kwargs.get("playit_config") or {}))
    return mgr


class _StubProcess:
    def __init__(self, status=ServerStatus.STOPPED):
        self.status = status
        self.start_calls = 0

    @property
    def is_running(self):
        return self.status in (ServerStatus.RUNNING, ServerStatus.STARTING, ServerStatus.STOPPING)

    def start(self):
        self.start_calls += 1
        self.status = ServerStatus.RUNNING
        return True


def test_wait_for_process_stop_already_stopped():
    assert ServerManager._wait_for_process_stop(_StubProcess(ServerStatus.STOPPED), timeout=1) is True


def test_wait_for_process_stop_waits_for_stopping():
    proc = _StubProcess(ServerStatus.STOPPING)

    def finish():
        import time

        time.sleep(0.2)
        proc.status = ServerStatus.STOPPED

    threading.Thread(target=finish, daemon=True).start()
    assert ServerManager._wait_for_process_stop(proc, timeout=5) is True


def test_wait_for_process_stop_times_out():
    proc = _StubProcess(ServerStatus.STOPPING)
    assert ServerManager._wait_for_process_stop(proc, timeout=0.3) is False


def test_ensure_bedrock_transport_skipped_without_tunnel(tmp_path):
    mgr = _manager_with_server(tmp_path)
    assert mgr.ensure_bedrock_transport("s1") is False
    assert not (tmp_path / "server.properties").exists()


def test_ensure_bedrock_transport_writes_raknet(tmp_path):
    mgr = _manager_with_server(tmp_path, playit_config={"bedrock_endpoint": "x.tun.ply.gg:1234"})
    (tmp_path / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
    assert mgr.ensure_bedrock_transport("s1") is True
    content = (tmp_path / "server.properties").read_text(encoding="utf-8")
    assert "transport=raknet" in content
    assert "server-port=25565" in content


def test_ensure_bedrock_transport_keeps_existing_raknet(tmp_path):
    mgr = _manager_with_server(tmp_path, playit_config={"bedrock_endpoint": "x.tun.ply.gg:1234"})
    (tmp_path / "server.properties").write_text("transport=raknet\n", encoding="utf-8")
    assert mgr.ensure_bedrock_transport("s1") is False


def test_ensure_bedrock_transport_overwrites_nethernet(tmp_path):
    mgr = _manager_with_server(tmp_path, playit_config={"bedrock_endpoint": "x.tun.ply.gg:1234"})
    (tmp_path / "server.properties").write_text("transport=nethernet\n", encoding="utf-8")
    assert mgr.ensure_bedrock_transport("s1") is True
    content = (tmp_path / "server.properties").read_text(encoding="utf-8")
    assert "transport=raknet" in content
    assert "nethernet" not in content


def test_start_server_waits_for_stopping_then_starts(tmp_path):
    from unittest.mock import patch

    mgr = _manager_with_server(tmp_path)
    proc = _StubProcess(ServerStatus.STOPPING)
    mgr._processes = {"s1": proc}
    mgr.java_manager = type("J", (), {"is_java_available": lambda self, v: True})()
    mgr.playit_manager = type("P", (), {"configure_voicechat_mod": lambda self, *a, **k: True})()

    def finish():
        import time

        time.sleep(0.2)
        proc.status = ServerStatus.STOPPED

    threading.Thread(target=finish, daemon=True).start()

    with (
        patch.object(ServerManager, "get_process", return_value=proc),
        patch.object(ServerManager, "is_mod_operation_active", return_value=False),
        patch.object(ServerManager, "check_port_conflict", return_value=None),
        patch.object(ServerManager, "check_bedrock_port_conflict", return_value=None),
        patch.object(ServerManager, "check_voicechat_port_conflict", return_value=None),
        patch.object(ServerManager, "get_voicechat_port", return_value=24454),
        patch.object(ServerManager, "ensure_multiplayer_icon", return_value=None),
        patch.object(ServerManager, "refresh_process_runtime", return_value=None),
    ):
        ok, error = mgr.start_server("s1")

    assert ok is True
    assert error is None
    assert proc.start_calls == 1


def test_start_server_stop_timeout(tmp_path):
    from unittest.mock import patch

    mgr = _manager_with_server(tmp_path)
    proc = _StubProcess(ServerStatus.STOPPING)
    mgr._processes = {"s1": proc}
    mgr.java_manager = type("J", (), {"is_java_available": lambda self, v: True})()

    with (
        patch.object(ServerManager, "get_process", return_value=proc),
        patch.object(ServerManager, "is_mod_operation_active", return_value=False),
        patch.object(ServerManager, "check_port_conflict", return_value=None),
        patch.object(ServerManager, "check_bedrock_port_conflict", return_value=None),
        patch.object(ServerManager, "check_voicechat_port_conflict", return_value=None),
        patch.object(ServerManager, "_wait_for_process_stop", return_value=False),
    ):
        ok, error = mgr.start_server("s1")

    assert ok is False
    assert error["kind"] == "stop-timeout"
    assert proc.start_calls == 0


def _manager_with_mc_version(tmp_path, mc_version="1.21.4"):
    from hosty.shared.backend.playit_config import save_playit_config

    mgr = ServerManager.__new__(ServerManager)
    mgr._servers = {
        "s1": ServerInfo({"id": "s1", "path": str(tmp_path), "loader_type": "fabric", "mc_version": mc_version})
    }
    save_playit_config(tmp_path, {})
    return mgr


def test_park_incompatible_mod_records_and_dedupes(tmp_path):

    mgr = _manager_with_mc_version(tmp_path)
    assert mgr.park_incompatible_mod("s1", "geyser", "Geyser", "1.21.4") is True
    assert mgr.park_incompatible_mod("s1", "geyser", "Geyser", "1.21.4") is False
    assert mgr.park_incompatible_mod("nope", "geyser", "Geyser", "1.21.4") is False
    records = mgr.get_incompatible_components("s1")["mods"]
    assert len(records) == 1
    assert records[0]["project_id"] == "geyser"
    assert records[0]["title"] == "Geyser"
    assert "1.21.4" in records[0]["reason"]


def test_retry_restores_parked_mod_when_compatible(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch

    mgr = _manager_with_mc_version(tmp_path)
    assert mgr.park_incompatible_mod("s1", "geyser", "Geyser", "1.21.4") is True

    version = SimpleNamespace(
        version_id="v1",
        filename="geyser-fabric-1.21.4.jar",
        download_url="https://example.invalid/geyser.jar",
        hashes={},
        version_number="1.0",
    )

    def fake_download(url, dest, expected_hashes=None):
        from pathlib import Path as _P

        p = _P(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"jar-bytes")

    with (
        patch("hosty.shared.backend.modrinth_client.find_compatible_version", return_value=version),
        patch("hosty.shared.backend.modrinth_client.resolve_required_dependencies", return_value=[]),
        patch("hosty.shared.backend.modrinth_client.download_to", side_effect=fake_download),
    ):
        restored, failed = mgr.retry_incompatible_components("s1")

    assert (restored, failed) == (1, 0)
    assert (tmp_path / "mods" / "geyser-fabric-1.21.4.jar").exists()
    assert mgr.get_incompatible_components("s1")["mods"] == []
    tracked = mgr._tracked_mod_state(tmp_path)
    assert tracked["geyser"]["version_id"] == "v1"


def test_retry_keeps_record_without_compatible_version(tmp_path):
    from unittest.mock import patch

    mgr = _manager_with_mc_version(tmp_path)
    assert mgr.park_incompatible_mod("s1", "simple-voice-chat", "Simple Voice Chat", "1.21.4") is True

    with patch("hosty.shared.backend.modrinth_client.find_compatible_version", return_value=None):
        assert mgr.retry_incompatible_components("s1") == (0, 0)
    assert len(mgr.get_incompatible_components("s1")["mods"]) == 1


def test_retry_skips_records_with_moved_aside_files(tmp_path):

    mgr = _manager_with_mc_version(tmp_path)
    (tmp_path / "mods_incompatible").mkdir(parents=True, exist_ok=True)
    (tmp_path / "mods_incompatible" / "old-mod.jar").write_bytes(b"old")
    data = mgr.get_incompatible_components("s1")
    data["mods"].append(
        {
            "title": "Old Mod",
            "filename": "old-mod.jar",
            "project_id": "old-mod",
            "reason": "No Modrinth mod release for Minecraft 1.21.4",
        }
    )
    mgr._write_json_file(tmp_path / ".hosty-incompatible-components.json", data)

    assert mgr.retry_incompatible_components("s1") == (0, 0)
    assert len(mgr.get_incompatible_components("s1")["mods"]) == 1
    assert (tmp_path / "mods_incompatible" / "old-mod.jar").exists()
