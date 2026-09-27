"""Tests for PlayitManager tunnel bookkeeping (no network access)."""

import builtins

import pytest


def _ensure_gettext():
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s  # noqa: E731


def test_create_tunnel_over_limit_reports_readable_message():
    """Over-limit creation must raise TunnelException, not UnboundLocalError.

    Regression test: the ``for _ in range(...)`` poll loop used to shadow
    gettext ``_()`` in ``_create_tunnel``, so building the limit message
    itself crashed with "cannot access local variable '_'".
    """
    _ensure_gettext()
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm.max_tunnels = 0  # block every creation attempt

    with pytest.raises(PlayitManager.TunnelException) as excinfo:
        pm._create_tunnel(24455, "udp", label="voicechat")

    assert "more than" in str(excinfo.value)


def test_get_tunnel_usage_counts_cached_tunnels():
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm.tunnels = {"tcp": [object()], "udp": [object(), object()], "both": []}
    pm.max_tunnels = 4

    assert pm.get_tunnel_usage() == (3, 4)


def test_get_tunnel_usage_defaults_when_unlinked():
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()

    used, maximum = pm.get_tunnel_usage()
    assert used == 0
    assert maximum >= 1
    assert pm.tunnels_refreshed_at is None


def test_retrieve_tunnels_without_agent_leaves_counts_unrefreshed():
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    assert pm._agent_id is None

    pm._retrieve_tunnels()

    assert pm.tunnels_refreshed_at is None
    assert pm.get_tunnel_usage()[0] == 0


def test_retrieve_tunnels_marks_counts_refreshed():
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    payload = {
        "status": "success",
        "data": {
            "tunnels": [
                {"id": "t1", "port_type": "tcp"},
                {"id": "t2", "port_type": "udp"},
            ],
            "tcp_alloc": {"allowed": 4},
            "udp_alloc": {"allowed": 4},
        },
    }

    with patch.object(PlayitManager, "_request", return_value=payload):
        pm._retrieve_tunnels()

    assert pm.tunnels_refreshed_at is not None
    assert pm.get_tunnel_usage() == (2, 4)


def _tunnel_data(
    tunnel_id="t-voice",
    proto="udp",
    tunnel_type="minecraft-bedrock",
    local_port=24454,
    domain="nicely-units.tun.ply.gg",
    remote_port=7701,
    name=None,
):
    return {
        "id": tunnel_id,
        "name": name if name is not None else f"hosty-x-{proto}-{local_port}",
        "tunnel_type": tunnel_type,
        "port_type": proto,
        "port_count": 1,
        "alloc": {
            "status": "active",
            "data": {
                "region": "global",
                "assigned_domain": domain,
                "port_start": remote_port,
            },
        },
        "origin": {"data": {"local_port": local_port, "local_ip": "127.0.0.1"}},
        "created_at": "",
    }


class _FakePlayitApi:
    """Stateful fake for tunnels/list|delete|update|create."""

    def __init__(self, store, allowed=4, fail_delete=False, ghost_delete=False):
        self.store = store
        self.allowed = allowed
        self.fail_delete = fail_delete
        self.ghost_delete = ghost_delete
        self.calls = []
        self.created = 0

    def __call__(self, endpoint, **kwargs):
        self.calls.append(endpoint)
        payload = kwargs.get("json", {})
        if endpoint == "tunnels/list":
            return {
                "status": "success",
                "data": {
                    "tunnels": list(self.store.values()),
                    "tcp_alloc": {"allowed": self.allowed},
                    "udp_alloc": {"allowed": self.allowed},
                },
            }
        if endpoint == "tunnels/delete":
            tunnel_id = str(payload.get("tunnel_id", ""))
            if self.fail_delete or tunnel_id not in self.store:
                return {"status": "fail"}
            if not self.ghost_delete:
                del self.store[tunnel_id]
            return {"status": "success"}
        if endpoint == "tunnels/update":
            tunnel_id = str(payload.get("tunnel_id", ""))
            assert "origin" not in payload, "must use the flat ReqTunnelsUpdate shape"
            try:
                self.store[tunnel_id]["origin"]["data"]["local_port"] = int(payload.get("local_port"))
            except Exception:
                return {"status": "fail"}
            return {"status": "success"}
        if endpoint == "tunnels/create":
            self.created += 1
            tunnel_id = f"t-new-{self.created}"
            origin = (payload.get("origin") or {}).get("data", {})
            self.store[tunnel_id] = _tunnel_data(
                tunnel_id,
                proto=payload.get("port_type", "udp"),
                tunnel_type=payload.get("tunnel_type"),
                local_port=int(origin.get("local_port", 24454)),
                domain="new.tun.ply.gg",
                remote_port=7800,
            )
            return {"status": "success", "data": {"id": tunnel_id}}
        if endpoint == "v1/tunnels/create":
            # Voice chat uses raw (typeless) tunnels: game-typed UDP tunnels
            # are protocol-filtered by playit's edges.
            self.created += 1
            tunnel_id = f"t-new-{self.created}"
            protocol = payload.get("protocol") or {}
            details = protocol.get("details") or {}
            assert protocol.get("type") == "raw-ports", "voice must use raw-ports protocol"
            assert details.get("port_type") == "udp"
            assert str(details.get("software_description") or "").strip(), "raw tunnels need a description"
            ep = payload.get("endpoint") or {}
            assert (ep.get("details") or {}).get("region") == "global"
            fields = (((payload.get("origin") or {}).get("data") or {}).get("config") or {}).get("fields", [])
            local_port = 24454
            for field in fields:
                if field.get("name") == "local_port":
                    local_port = int(field.get("value", 24454))
            self.store[tunnel_id] = _tunnel_data(
                tunnel_id,
                proto="udp",
                tunnel_type=None,
                local_port=local_port,
                domain="new.tun.ply.gg",
                remote_port=7800,
                name=payload.get("name"),
            )
            return {"status": "success", "data": {"id": tunnel_id}}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    def patch(self, pm):
        from unittest.mock import patch

        from hosty.shared.backend.playit_manager import PlayitManager

        return (
            patch.object(PlayitManager, "_ensure_api_ready", return_value=(True, "")),
            patch.object(PlayitManager, "_request", side_effect=self),
            patch.object(pm.tunnel_cache, "add_tunnel", return_value=True),
            patch.object(pm.tunnel_cache, "remove_tunnel", return_value=True),
        )


def _regen_manager(tmp_path):
    from hosty.shared.backend.playit_config import save_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    save_playit_config(
        tmp_path,
        {"voicechat_endpoint": "nicely-units.tun.ply.gg:7701", "voicechat_port": 24455},
    )
    return pm


def test_regenerate_finds_drifted_tunnel_via_stored_endpoint(tmp_path):
    """Config says 24455 but the tunnel still forwards 24454: regenerate must
    still find and replace it via the stored public endpoint."""
    from contextlib import ExitStack

    pm = _regen_manager(tmp_path)
    store = {"t-voice": _tunnel_data()}
    api = _FakePlayitApi(store, allowed=4)

    with ExitStack() as stack:
        for ctx in api.patch(pm):
            stack.enter_context(ctx)
        ok, msg, endpoint = pm.regenerate_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24455)

    assert ok is True
    assert endpoint == "new.tun.ply.gg:7800"
    assert "t-voice" not in store


def test_regenerate_aborts_when_old_tunnel_cannot_be_deleted(tmp_path):
    """A failed delete must not be followed by a doomed create attempt."""
    from contextlib import ExitStack
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = _regen_manager(tmp_path)
    store = {"t-voice": _tunnel_data()}
    api = _FakePlayitApi(store, allowed=4, fail_delete=True)

    with ExitStack() as stack:
        for ctx in api.patch(pm):
            stack.enter_context(ctx)
        stack.enter_context(
            patch.object(
                PlayitManager,
                "_add_tunnel_for_protocol",
                side_effect=AssertionError("doomed create attempted"),
            )
        )
        ok, msg, endpoint = pm.regenerate_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24455)

    assert ok is False
    assert endpoint == ""
    assert "Could not remove" in msg
    assert "t-voice" in store


def test_regenerate_aborts_when_still_at_cap_after_delete(tmp_path):
    """Ghost entries (API still listing the deleted tunnel) must not trigger
    a doomed create; the message must state current usage."""
    from contextlib import ExitStack
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = _regen_manager(tmp_path)
    store = {
        "t-java": _tunnel_data(
            "t-java",
            proto="tcp",
            tunnel_type="minecraft-java",
            local_port=25565,
            domain="java.tun.ply.gg",
            remote_port=25565,
        ),
        "t-voice": _tunnel_data(),
        "t-old-1": _tunnel_data("t-old-1", local_port=24460, domain="old1.tun.ply.gg", remote_port=7601),
        "t-old-2": _tunnel_data("t-old-2", local_port=24461, domain="old2.tun.ply.gg", remote_port=7602),
    }
    api = _FakePlayitApi(store, allowed=4, ghost_delete=True)

    with ExitStack() as stack:
        for ctx in api.patch(pm):
            stack.enter_context(ctx)
        stack.enter_context(
            patch.object(
                PlayitManager,
                "_add_tunnel_for_protocol",
                side_effect=AssertionError("doomed create attempted"),
            )
        )
        ok, msg, endpoint = pm.regenerate_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24455)

    assert ok is False
    assert endpoint == ""
    assert "4 of 4" in msg


def test_regenerate_at_cap_reports_readable_message():
    """With no matching tunnel and no room, the cap error must be readable
    (no UnboundLocalError) instead of a crash."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        f"t-tcp-{index}": _tunnel_data(
            f"t-tcp-{index}",
            proto="tcp",
            tunnel_type="minecraft-java",
            local_port=25560 + index,
            domain=f"java{index}.tun.ply.gg",
            remote_port=25560 + index,
        )
        for index in range(4)
    }
    api = _FakePlayitApi(store, allowed=4)

    with (
        patch.object(PlayitManager, "_ensure_api_ready", return_value=(True, "")),
        patch.object(PlayitManager, "_request", side_effect=api),
        patch.object(pm.tunnel_cache, "add_tunnel", return_value=True),
        patch.object(pm.tunnel_cache, "remove_tunnel", return_value=True),
    ):
        ok, msg, endpoint = pm._regenerate_tunnel_for_protocol(
            "srv", "/nonexistent", "udp", tunnel_kind="voicechat", voicechat_port=24455
        )

    assert ok is False
    assert endpoint == ""
    assert "more than" in msg


def _list_payload(tunnels, allowed=1):
    return {
        "status": "success",
        "data": {
            "tunnels": tunnels,
            "tcp_alloc": {"allowed": allowed},
            "udp_alloc": {"allowed": allowed},
        },
    }


def test_ensure_free_slot_noop_when_under_cap():
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    calls = []

    def fake_request(endpoint, **kwargs):
        calls.append(endpoint)
        return _list_payload([_tunnel_data()], allowed=4)

    with patch.object(PlayitManager, "_request", side_effect=fake_request):
        assert pm.ensure_free_slot(24454, "udp") is True

    assert "tunnels/delete" not in calls


def test_ensure_free_slot_deletes_obsolete_tunnel_at_cap():
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    calls = []

    def fake_request(endpoint, **kwargs):
        calls.append(endpoint)
        if endpoint == "tunnels/delete":
            return {"status": "success"}
        return _list_payload([_tunnel_data()], allowed=1)

    with patch.object(PlayitManager, "_request", side_effect=fake_request):
        assert pm.ensure_free_slot(24454, "udp") is True

    assert "tunnels/delete" in calls
    assert pm.get_tunnel_usage() == (0, 1)


def test_ensure_free_slot_still_capped_when_nothing_on_port():
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"

    def fake_request(endpoint, **kwargs):
        return _list_payload([_tunnel_data()], allowed=1)

    with patch.object(PlayitManager, "_request", side_effect=fake_request):
        assert pm.ensure_free_slot(29999, "udp") is False


def test_start_registers_server_dir():
    """Server registrations must carry server_dir so the agent can restart."""
    _ensure_gettext()
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()

    with (
        patch.object(PlayitManager, "resolve_binary", return_value="/bin/playit"),
        patch.object(PlayitManager, "_is_pinned_binary", return_value=True),
        patch.object(PlayitManager, "read_claimed_secret", return_value="secret-1"),
        patch.object(PlayitManager, "_initialize_with_retry", return_value=True),
        patch.object(PlayitManager, "_start_agent_service", return_value=True),
    ):
        ok, _msg = pm.start("srv-1", "/srv/dir-1")

    assert ok is True
    assert pm._active_server_ids["srv-1"]["server_dir"] == "/srv/dir-1"


def test_restart_agent_preserves_registrations():
    """Restart must relaunch for the same servers (needs stored server_dir)."""
    from unittest.mock import Mock, patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._process = Mock()
    pm._process.poll.return_value = None
    pm._active_server_ids = {
        "srv-1": {"tunnel_id": "t-1", "endpoint": "a.example:1", "port": 25565, "server_dir": "/srv/dir-1"},
        "srv-2": {"tunnel_id": None, "endpoint": "", "port": None, "server_dir": "/srv/dir-2"},
        "srv-legacy": {"tunnel_id": None, "endpoint": "", "port": None},
    }
    started = []

    def fake_start(server_id, server_dir, **kwargs):
        started.append((server_id, server_dir))
        pm._active_server_ids[server_id] = {
            "tunnel_id": None,
            "endpoint": "",
            "port": None,
            "server_dir": server_dir,
        }
        return True, ""

    with patch.object(PlayitManager, "start", side_effect=fake_start):
        ok, _msg = pm.restart_agent()

    assert ok is True
    assert sorted(started) == [("srv-1", "/srv/dir-1"), ("srv-2", "/srv/dir-2")]
    assert pm._active_server_ids["srv-1"] == {
        "tunnel_id": "t-1",
        "endpoint": "a.example:1",
        "port": 25565,
        "server_dir": "/srv/dir-1",
    }


def test_restart_agent_when_stopped_does_nothing():
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    assert pm._process is None

    with patch.object(PlayitManager, "start", side_effect=AssertionError("must not start")):
        ok, _msg = pm.restart_agent()

    assert ok is True


def test_restart_agent_keeps_running_when_nothing_registered():
    """Legacy entries without server_dir must not strand the agent stopped."""
    from unittest.mock import Mock, patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._process = Mock()
    pm._process.poll.return_value = None
    pm._active_server_ids = {
        "srv-legacy": {"tunnel_id": None, "endpoint": "", "port": None},
    }

    with patch.object(PlayitManager, "start", side_effect=AssertionError("must not start")):
        ok, _msg = pm.restart_agent()

    assert ok is True
    assert pm._process is not None
    assert "srv-legacy" in pm._active_server_ids


class _LinkSuccessResponse:
    status_code = 200
    text = '{"status": "success"}'

    def json(self):
        return {"status": "success", "data": {"agent_id": "agent-9", "agent_secret_key": "secret-9"}}


def test_link_account_restarts_running_agent():
    """Relink changes identity: a running process would keep serving the old
    agent, so link must restart it (new tunnels would otherwise stay dark)."""
    _ensure_gettext()
    from unittest.mock import Mock, patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._process = Mock()
    pm._process.poll.return_value = None
    restarts = []

    def fake_restart():
        restarts.append(True)
        return True, "restarted"

    with (
        patch.object(PlayitManager, "resolve_binary", return_value="/bin/playit"),
        patch.object(PlayitManager, "_detect_version", return_value=(0, 17, 1)),
        patch("requests.post", return_value=_LinkSuccessResponse()),
        patch.object(PlayitManager, "_write_secret_key", return_value=True),
        patch.object(PlayitManager, "_initialize_with_retry", return_value=True),
        patch.object(PlayitManager, "restart_agent", side_effect=fake_restart),
    ):
        ok, msg = pm.link_account("code-123")

    assert ok is True
    assert "linked" in msg
    assert restarts == [True]
    assert pm._agent_id == "agent-9"


def test_link_account_skips_restart_when_stopped():
    _ensure_gettext()
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    assert pm._process is None

    with (
        patch.object(PlayitManager, "resolve_binary", return_value="/bin/playit"),
        patch.object(PlayitManager, "_detect_version", return_value=(0, 17, 1)),
        patch("requests.post", return_value=_LinkSuccessResponse()),
        patch.object(PlayitManager, "_write_secret_key", return_value=True),
        patch.object(PlayitManager, "_initialize_with_retry", return_value=True),
        patch.object(PlayitManager, "restart_agent", side_effect=AssertionError("must not restart")),
    ):
        ok, _msg = pm.link_account("code-123")

    assert ok is True


def _alloc_patches(pm, api):
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    return (
        patch.object(PlayitManager, "_ensure_api_ready", return_value=(True, "")),
        patch.object(PlayitManager, "_request", side_effect=api),
        patch.object(pm.tunnel_cache, "add_tunnel", return_value=True),
        patch.object(pm.tunnel_cache, "remove_tunnel", return_value=True),
    )


def _save_voice_cfg(tmp_path, **overrides):
    from hosty.shared.backend.playit_config import load_playit_config, save_playit_config

    cfg = load_playit_config(tmp_path)
    cfg.update(overrides)
    assert save_playit_config(tmp_path, cfg)
    return load_playit_config(tmp_path)


def test_bedrock_request_does_not_steal_voice_tunnel(tmp_path):
    """The reported collision: a bedrock request must not retarget the
    voice tunnel. A second, separate tunnel has to be created."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {"t-voice": _tunnel_data("t-voice", local_port=24454, name="hosty-voicechat-udp-24454-1")}
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, bedrock_port=19132)

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_bedrock_tunnel("srv", str(tmp_path), bedrock_port=19132)

    assert ok is True
    assert endpoint == "new.tun.ply.gg:7800"
    assert api.created == 1
    assert store["t-voice"]["origin"]["data"]["local_port"] == 24454
    assert load_playit_config(tmp_path)["bedrock_tunnel_id"] != "t-voice"


def test_voice_request_does_not_steal_bedrock_tunnel(tmp_path):
    """Mirror direction: voice must not adopt a bedrock-named tunnel."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        "t-bed": _tunnel_data(
            "t-bed",
            local_port=19132,
            domain="bed.tun.ply.gg",
            remote_port=7601,
            name="hosty-bedrock-udp-19132-1",
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, voicechat_port=24454)

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert endpoint == "new.tun.ply.gg:7800"
    assert store["t-bed"]["origin"]["data"]["local_port"] == 19132
    assert load_playit_config(tmp_path)["voicechat_tunnel_id"] != "t-bed"


def test_stored_tunnel_id_is_reused_and_retargeted(tmp_path):
    """Port change on a known tunnel keeps the public endpoint (no slot
    needed, no new address)."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {"t-voice": _tunnel_data("t-voice", local_port=24454, name="hosty-voicechat-udp-24454-1", tunnel_type=None)}
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24455,
        voicechat_tunnel_id="t-voice",
        voicechat_endpoint="nicely-units.tun.ply.gg:7701",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24455)

    assert ok is True
    assert endpoint == "nicely-units.tun.ply.gg:7701"
    assert api.created == 0
    assert "tunnels/update" in api.calls
    assert store["t-voice"]["origin"]["data"]["local_port"] == 24455


def test_legacy_tunnel_adopted_by_port_and_kind(tmp_path):
    """A pre-ID Hosty voice tunnel on the wanted port is adopted, not duplicated."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {"t-voice": _tunnel_data("t-voice", local_port=24454, name="hosty-voicechat-udp-24454-9", tunnel_type=None)}
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, voicechat_port=24454)

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert endpoint == "nicely-units.tun.ply.gg:7701"
    assert api.created == 0
    assert load_playit_config(tmp_path)["voicechat_tunnel_id"] == "t-voice"


def test_handmade_tunnel_is_never_adopted_or_retargeted(tmp_path):
    """Dashboard-made tunnels are left alone even on a matching port."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        "t-user": _tunnel_data(
            "t-user",
            local_port=24454,
            domain="user.example.com",
            remote_port=9999,
            name="my-custom-udp",
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, voicechat_port=24454)

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert endpoint == "new.tun.ply.gg:7800"
    assert api.created == 1
    assert store["t-user"]["origin"]["data"]["local_port"] == 24454


def test_auto_create_clears_stale_auto_endpoint(tmp_path):
    """Endpoints deleted via the dashboard are cleared so the UI stops
    showing dead domains."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    _save_voice_cfg(
        tmp_path,
        voicechat_endpoint="dead.tun.ply.gg:1111",
        voicechat_port=24454,
    )
    pm = PlayitManager()
    pm._agent_id = "agent-1"

    with patch.object(
        PlayitManager,
        "_request",
        return_value={
            "status": "success",
            "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
        },
    ):
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454, loader="fabric")

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "", "voicechat_migrated": ""}
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == ""


def test_delete_voicechat_tunnel_keeps_id_as_tombstone(tmp_path):
    """Deleting keeps the tunnel id so auto-create does not resurrect what
    the user just deleted. The id is overwritten on the next explicit add."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {"t-voice": _tunnel_data("t-voice", local_port=24454)}
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_endpoint="nicely-units.tun.ply.gg:7701",
        voicechat_port=24454,
        voicechat_tunnel_id="t-voice",
    )

    with (
        patch.object(PlayitManager, "_ensure_api_ready", return_value=(True, "")),
        patch.object(PlayitManager, "_request", side_effect=api),
        patch.object(pm.tunnel_cache, "remove_tunnel", return_value=True),
    ):
        ok, _msg = pm.delete_voicechat_tunnel(str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert "t-voice" not in store
    assert load_playit_config(tmp_path)["voicechat_tunnel_id"] == "t-voice"


def test_deleted_tunnel_is_not_resurrected_on_agent_start(tmp_path):
    """Dashboard/UI deletion + agent start must leave the endpoint cleared
    (mod still installed) instead of creating a replacement tunnel."""
    from pathlib import Path
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    mods_dir = Path(tmp_path) / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "simple-voice-chat-fabric-1.0.jar").write_text("fake")
    _save_voice_cfg(
        tmp_path,
        voicechat_endpoint="",
        voicechat_tunnel_id="t-gone",
        voicechat_port=24454,
    )
    pm = PlayitManager()
    pm._agent_id = "agent-1"

    with (
        patch.object(
            PlayitManager,
            "_request",
            return_value={
                "status": "success",
                "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
            },
        ),
        patch.object(PlayitManager, "add_voicechat_tunnel", side_effect=AssertionError("must not resurrect")),
        patch.object(PlayitManager, "add_bedrock_tunnel", side_effect=AssertionError("must not resurrect")),
    ):
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454, loader="fabric")

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "", "voicechat_migrated": ""}
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == ""
    assert load_playit_config(tmp_path)["voicechat_tunnel_id"] == "t-gone"


def test_fresh_install_gets_tunnel_created(tmp_path):
    """A server that never had a tunnel (no id) still gets one auto-created
    for an installed mod."""
    from pathlib import Path
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    mods_dir = Path(tmp_path) / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "simple-voice-chat-fabric-1.0.jar").write_text("fake")
    _save_voice_cfg(tmp_path, voicechat_endpoint="", voicechat_port=24454)
    pm = PlayitManager()
    pm._agent_id = "agent-1"

    with (
        patch.object(
            PlayitManager,
            "_request",
            return_value={
                "status": "success",
                "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
            },
        ),
        patch.object(
            PlayitManager, "add_voicechat_tunnel", return_value=(True, "created", "new.tun.ply.gg:7800")
        ) as add_mock,
    ):
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454, loader="fabric")

    assert add_mock.call_count == 1
    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "new.tun.ply.gg:7800", "voicechat_migrated": ""}
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == "new.tun.ply.gg:7800"


def test_auto_create_emits_when_it_mutates_config(tmp_path):
    """Views cache the config: a validation clear must notify them or the
    deleted tunnel keeps being displayed."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    _save_voice_cfg(
        tmp_path,
        voicechat_endpoint="dead.tun.ply.gg:1111",
        voicechat_port=24454,
    )
    pm = PlayitManager()
    pm._agent_id = "agent-1"
    emitted = []
    pm.connect("endpoint-changed", lambda *args: emitted.append(args))

    with patch.object(
        PlayitManager,
        "_request",
        return_value={
            "status": "success",
            "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
        },
    ):
        pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454, loader="fabric")

    assert emitted, "expected endpoint-changed after clearing a stale endpoint"


def test_auto_create_stays_quiet_when_nothing_changes(tmp_path):
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    _save_voice_cfg(tmp_path, voicechat_endpoint="", voicechat_port=24454)
    pm = PlayitManager()
    pm._agent_id = "agent-1"
    emitted = []
    pm.connect("endpoint-changed", lambda *args: emitted.append(args))

    with patch.object(
        PlayitManager,
        "_request",
        return_value={
            "status": "success",
            "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
        },
    ):
        pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454, loader="fabric")

    assert emitted == []


def test_auto_create_skips_validation_without_fresh_list(tmp_path):
    """A failed tunnel refresh must not be mistaken for deletions: without
    fresh data, stored endpoints are left untouched, never wiped."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    _save_voice_cfg(
        tmp_path,
        voicechat_endpoint="maybe.tun.ply.gg:7701",
        voicechat_port=24454,
    )
    pm = PlayitManager()
    pm._agent_id = "agent-1"
    assert pm.tunnels_refreshed_at is None

    with patch.object(PlayitManager, "_request", side_effect=RuntimeError("offline")):
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454, loader="fabric")

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "", "voicechat_migrated": ""}
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == "maybe.tun.ply.gg:7701"


def test_bedrock_failure_does_not_skip_voice_validation(tmp_path):
    """Each kind is independent: a bedrock explosion must not prevent voice
    validation from clearing its stale endpoint."""
    from pathlib import Path
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    mods_dir = Path(tmp_path) / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "geyser-fabric-1.0.jar").write_text("fake")
    (mods_dir / "simple-voice-chat-fabric-1.0.jar").write_text("fake")
    _save_voice_cfg(
        tmp_path,
        bedrock_endpoint="dead-bedrock.tun.ply.gg:7601",
        voicechat_endpoint="dead-voice.tun.ply.gg:7701",
        bedrock_port=19132,
        voicechat_port=24454,
    )
    pm = PlayitManager()
    pm._agent_id = "agent-1"

    with (
        patch.object(
            PlayitManager,
            "_request",
            return_value={
                "status": "success",
                "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
            },
        ),
        patch.object(PlayitManager, "add_bedrock_tunnel", side_effect=RuntimeError("bedrock exploded")),
        patch.object(PlayitManager, "add_voicechat_tunnel", return_value=(False, "nope", "")),
    ):
        result = pm.auto_create_tunnel_mods(
            "srv", str(tmp_path), bedrock_port=19132, voicechat_port=24454, loader="fabric"
        )

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "", "voicechat_migrated": ""}
    cfg = load_playit_config(tmp_path)
    assert cfg["bedrock_endpoint"] == ""
    assert cfg["voicechat_endpoint"] == ""


def test_auto_create_clears_stale_java_endpoint(tmp_path):
    """The Java endpoint previously had no validation path: a dashboard-
    deleted Java tunnel stayed displayed forever."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    _save_voice_cfg(tmp_path, java_endpoint="dead-java.tun.ply.gg")
    pm = PlayitManager()
    pm._agent_id = "agent-1"

    with patch.object(
        PlayitManager,
        "_request",
        return_value={
            "status": "success",
            "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
        },
    ):
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), loader="fabric")

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "", "voicechat_migrated": ""}
    assert load_playit_config(tmp_path)["java_endpoint"] == ""


def test_auto_create_keeps_live_java_endpoint(tmp_path):
    from unittest.mock import patch

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    live = _tunnel_data(
        "t-java",
        proto="tcp",
        tunnel_type="minecraft-java",
        local_port=25565,
        domain="live.tun.ply.gg",
        remote_port=40000,
        name="hosty-srv-tcp-25565-1",
    )
    _save_voice_cfg(tmp_path, java_endpoint="live.tun.ply.gg")
    pm = PlayitManager()
    pm._agent_id = "agent-1"

    def fake_request(endpoint, **kwargs):
        if endpoint == "tunnels/list":
            return {
                "status": "success",
                "data": {"tunnels": [live], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}},
            }
        raise AssertionError(endpoint)

    with patch.object(PlayitManager, "_request", side_effect=fake_request):
        pm.auto_create_tunnel_mods("srv", str(tmp_path), loader="fabric")

    assert load_playit_config(tmp_path)["java_endpoint"] == "live.tun.ply.gg"


def test_null_tunnel_ids_normalize_to_empty(tmp_path):
    """Nulls in the config file ( alien writes) must not become "None"."""
    from hosty.shared.backend.playit_config import load_playit_config, save_playit_config

    raw = dict(load_playit_config(tmp_path))
    raw.update(
        {
            "java_tunnel_id": None,
            "bedrock_tunnel_id": None,
            "voicechat_tunnel_id": None,
            "voicechat_endpoint": None,
            "secret": None,
        }
    )
    assert save_playit_config(tmp_path, raw) is True
    cfg = load_playit_config(tmp_path)
    assert cfg["java_tunnel_id"] == ""
    assert cfg["bedrock_tunnel_id"] == ""
    assert cfg["voicechat_tunnel_id"] == ""
    assert cfg["voicechat_endpoint"] == ""
    assert cfg["secret"] == ""


def test_retrieve_dedupes_echoed_tunnels_by_id():
    """tunnels/list sometimes echoes the same tunnel twice (e.g. around
    create/delete). It must be counted once, preferring the active record."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pending_echo = {
        "id": "t-dup",
        "name": "hosty-voicechat-udp-24454-1",
        "tunnel_type": "minecraft-bedrock",
        "port_type": "udp",
        "port_count": 1,
        "alloc": {"status": "pending"},
        "origin": {"data": {"local_port": 24454, "local_ip": "127.0.0.1"}},
        "created_at": "",
    }
    pm = PlayitManager()
    pm._agent_id = "agent-1"
    payload = {
        "status": "success",
        "data": {
            "tunnels": [
                _tunnel_data(
                    "t-tcp",
                    proto="tcp",
                    tunnel_type="minecraft-java",
                    local_port=25565,
                    domain="a.tun.ply.gg",
                    remote_port=10001,
                    name="hosty-srv-tcp-25565-1",
                ),
                pending_echo,
                _tunnel_data(
                    "t-dup",
                    local_port=24454,
                    domain="b.tun.ply.gg",
                    remote_port=10002,
                    name="hosty-voicechat-udp-24454-1",
                ),
                _tunnel_data(
                    "t-dup",
                    local_port=24454,
                    domain="b.tun.ply.gg",
                    remote_port=10002,
                    name="hosty-voicechat-udp-24454-1",
                ),
            ],
            "tcp_alloc": {"allowed": 4},
            "udp_alloc": {"allowed": 4},
        },
    }

    with patch.object(PlayitManager, "_request", return_value=payload):
        pm._retrieve_tunnels()

    assert pm.get_tunnel_usage() == (2, 4)
    kept = [t for t in pm.tunnels["udp"] if t.id == "t-dup"]
    assert len(kept) == 1
    assert kept[0].status != "pending"
    assert kept[0].port == 24454


def _tcp_tunnel_data(tunnel_id="t-java", local_port=25565, domain="java.tun.ply.gg", remote_port=40000):
    return _tunnel_data(
        tunnel_id,
        proto="tcp",
        tunnel_type="minecraft-java",
        local_port=local_port,
        domain=domain,
        remote_port=remote_port,
        name=f"hosty-srv-tcp-{local_port}-1",
    )


def _empty_success():
    return {"status": "success", "data": {"tunnels": [], "tcp_alloc": {"allowed": 4}, "udp_alloc": {"allowed": 4}}}


def test_failed_retrieve_keeps_last_known_tunnels():
    """A failed refresh (API blip during startup) must not wipe good data:
    the usage row would otherwise show 0/4 until something refetches."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    good = {
        "status": "success",
        "data": {
            "tunnels": [_tcp_tunnel_data()],
            "tcp_alloc": {"allowed": 4},
            "udp_alloc": {"allowed": 4},
        },
    }

    with patch.object(PlayitManager, "_request", return_value=good):
        pm._retrieve_tunnels()
    assert pm.get_tunnel_usage() == (1, 4)
    mark = pm.tunnels_refreshed_at
    assert mark is not None

    with patch.object(PlayitManager, "_request", side_effect=RuntimeError("blip")):
        pm._retrieve_tunnels()

    assert pm.get_tunnel_usage() == (1, 4)
    assert pm.tunnels_refreshed_at is mark
    assert [t.id for t in pm.tunnels["tcp"]] == ["t-java"]


def test_successful_retrieve_replaces_tunnel_data():
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    first = {
        "status": "success",
        "data": {
            "tunnels": [_tcp_tunnel_data("t-old")],
            "tcp_alloc": {"allowed": 4},
            "udp_alloc": {"allowed": 4},
        },
    }
    second = {
        "status": "success",
        "data": {
            "tunnels": [_tcp_tunnel_data("t-new")],
            "tcp_alloc": {"allowed": 4},
            "udp_alloc": {"allowed": 4},
        },
    }

    with patch.object(PlayitManager, "_request", return_value=first):
        pm._retrieve_tunnels()
    with patch.object(PlayitManager, "_request", return_value=second):
        pm._retrieve_tunnels()

    assert [t.id for t in pm.tunnels["tcp"]] == ["t-new"]
    assert pm.get_tunnel_usage() == (1, 4)


def test_unlink_resets_tunnel_freshness():
    """After unlink the row must show Not available, not stale counts."""
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm.tunnels["tcp"] = ["whatever"]
    pm.tunnels_refreshed_at = 1234.5

    pm.unlink_account()

    assert pm.tunnels == {"tcp": [], "udp": [], "both": []}
    assert pm.tunnels_refreshed_at is None


def test_legacy_bedrock_voice_tunnel_is_replaced_with_raw(tmp_path):
    """A stored bedrock-typed voice tunnel is protocol-filtered by playit's
    edges (voice packets dropped), so it must be replaced with a raw tunnel."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert endpoint == "new.tun.ply.gg:7800"
    assert "tunnels/delete" in api.calls
    assert "v1/tunnels/create" in api.calls
    assert "t-legacy" not in store
    assert store["t-new-1"]["tunnel_type"] is None
    assert load_playit_config(tmp_path)["voicechat_tunnel_id"] == "t-new-1"


def test_legacy_voice_tunnel_kept_when_delete_fails(tmp_path):
    """If the legacy tunnel cannot be deleted, keep serving it rather than
    stranding the server without an endpoint."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4, fail_delete=True)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert endpoint == "old.tun.ply.gg:1111"
    assert "v1/tunnels/create" not in api.calls
    assert "t-legacy" in store


def test_bedrock_typed_tunnel_is_not_adopted_for_voice(tmp_path):
    """A bedrock-typed tunnel on the voice port must not be adopted for
    voice chat: it would be protocol-filtered. A raw tunnel is created."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        "t-bed": _tunnel_data(
            "t-bed",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, voicechat_port=24454)

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24454)

    assert ok is True
    assert endpoint == "new.tun.ply.gg:7800"
    assert "tunnels/delete" not in api.calls
    assert "t-bed" in store


def test_raw_voice_tunnel_is_reused_and_retargeted(tmp_path):
    """Raw voice tunnels keep the old reuse behavior (no slot needed)."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    store = {
        "t-voice": _tunnel_data(
            "t-voice",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type=None,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24455,
        voicechat_tunnel_id="t-voice",
        voicechat_endpoint="nicely-units.tun.ply.gg:7701",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        ok, _msg, endpoint = pm.add_voicechat_tunnel("srv", str(tmp_path), voicechat_port=24455)

    assert ok is True
    assert endpoint == "nicely-units.tun.ply.gg:7701"
    assert api.created == 0
    assert "tunnels/delete" not in api.calls


def _auto_create_pm(tmp_path):
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    pm.directory = tmp_path / "playit"
    return pm


def test_auto_create_migrates_legacy_voice_by_stored_id(tmp_path):
    """The reported miss: a server that already had a bedrock-typed voice
    tunnel must get it replaced with a raw one on start, with the account
    map recording old -> new."""
    import json
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert result["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    assert "t-legacy" not in store
    assert store["t-new-1"]["tunnel_type"] is None
    cfg = load_playit_config(tmp_path)
    assert cfg["voicechat_tunnel_id"] == "t-new-1"
    assert cfg["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    mmap = json.loads((tmp_path / "playit" / ".voice-migrated.json").read_text(encoding="utf-8"))
    assert mmap["t-legacy"] == {"id": "t-new-1", "endpoint": "new.tun.ply.gg:7800"}


def test_auto_create_migrates_legacy_voice_by_endpoint_without_id(tmp_path):
    """Servers that share an endpoint but store no tunnel id (the second
    server) must migrate too, and record their new id."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, voicechat_port=24454, voicechat_endpoint="old.tun.ply.gg:1111")

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert result["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    cfg = load_playit_config(tmp_path)
    assert cfg["voicechat_tunnel_id"] == "t-new-1"


def test_auto_create_does_not_steal_bedrock_endpoint_for_voice(tmp_path):
    """A voice endpoint pointing at a bedrock-kind tunnel is a
    misconfiguration, not a migration candidate: leave it alone."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-bed": _tunnel_data(
            "t-bed",
            local_port=19132,
            name="hosty-bedrock-udp-19132-1",
            tunnel_type="minecraft-bedrock",
            domain="bed.tun.ply.gg",
            remote_port=2222,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(tmp_path, voicechat_port=24454, voicechat_endpoint="bed.tun.ply.gg:2222")

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert result["voicechat_endpoint"] == ""
    assert "tunnels/delete" not in api.calls
    assert "v1/tunnels/create" not in api.calls
    assert "t-bed" in store
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == "bed.tun.ply.gg:2222"


def test_auto_create_heals_shared_tunnel_via_migration_map(tmp_path):
    """Server B shares server A's legacy tunnel. A migrates (map recorded);
    B heals from the map on its next start instead of stranding."""
    import json
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    (tmp_path / "playit").mkdir(parents=True, exist_ok=True)
    (tmp_path / "playit" / ".voice-migrated.json").write_text(
        json.dumps({"t-legacy": {"id": "t-new-1", "endpoint": "new.tun.ply.gg:7800"}}),
        encoding="utf-8",
    )
    store = {
        "t-new-1": _tunnel_data(
            "t-new-1",
            local_port=24454,
            name="hosty-voicechat-udp-24454-2",
            tunnel_type=None,
            domain="new.tun.ply.gg",
            remote_port=7800,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        pm.auto_create_tunnel_mods("srv-b", str(tmp_path), voicechat_port=24454)

    cfg = load_playit_config(tmp_path)
    assert cfg["voicechat_tunnel_id"] == "t-new-1"
    assert cfg["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    assert api.created == 0


def test_auto_create_clears_mapped_endpoint_when_replacement_is_gone(tmp_path):
    """If the mapped replacement was later deleted, validation clears the
    endpoint instead of displaying a dead domain (id stays as tombstone)."""
    import json
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    (tmp_path / "playit").mkdir(parents=True, exist_ok=True)
    (tmp_path / "playit" / ".voice-migrated.json").write_text(
        json.dumps({"t-legacy": {"id": "t-new-1", "endpoint": "new.tun.ply.gg:7800"}}),
        encoding="utf-8",
    )
    api = _FakePlayitApi({}, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        pm.auto_create_tunnel_mods("srv-b", str(tmp_path), voicechat_port=24454)

    cfg = load_playit_config(tmp_path)
    assert cfg["voicechat_endpoint"] == ""
    assert cfg["voicechat_tunnel_id"] == "t-new-1"


def test_auto_create_migration_reports_new_endpoint(tmp_path):
    """The UI toast relies on result['voicechat_migrated'] being set."""
    from contextlib import ExitStack

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert result["voicechat_migrated"] == "new.tun.ply.gg:7800"


def test_auto_create_migration_skipped_when_port_shared(tmp_path):
    """Regenerate deletes by port: if another tunnel shares the voice port,
    auto-migration must not run (manual regenerate stays available)."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        ),
        "t-other": _tunnel_data(
            "t-other",
            local_port=24454,
            name="hosty-bedrock-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="other.tun.ply.gg",
            remote_port=2222,
        ),
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert result["voicechat_migrated"] == ""
    assert "tunnels/delete" not in api.calls
    assert "t-legacy" in store and "t-other" in store
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == "old.tun.ply.gg:1111"


def test_auto_create_migration_skipped_over_cap(tmp_path):
    """Delete-then-create while already over the account cap could strand
    with nothing: skip instead."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        ),
    }
    for i in range(4):
        store[f"t-fill-{i}"] = _tunnel_data(
            f"t-fill-{i}",
            proto="tcp",
            tunnel_type="minecraft-java",
            local_port=25560 + i,
            domain=f"fill{i}.tun.ply.gg",
            remote_port=5000 + i,
            name=f"hosty-fill-{i}",
        )
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        voicechat_port=24454,
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result = pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert result["voicechat_migrated"] == ""
    assert "tunnels/delete" not in api.calls
    assert "t-legacy" in store
    assert load_playit_config(tmp_path)["voicechat_endpoint"] == "old.tun.ply.gg:1111"


def test_shared_legacy_voice_tunnel_migrates_once_for_both_servers(tmp_path):
    """Two servers sharing one bedrock-typed voice tunnel: the first start
    migrates (single raw replacement), the second heals from the map."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = tmp_path / "srvA"
    dir_b = tmp_path / "srvB"
    dir_a.mkdir()
    dir_b.mkdir()
    for d in (dir_a, dir_b):
        _save_voice_cfg(
            d,
            voicechat_port=24454,
            voicechat_tunnel_id="t-legacy",
            voicechat_endpoint="old.tun.ply.gg:1111",
        )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        result_a = pm.auto_create_tunnel_mods("srv-a", str(dir_a), voicechat_port=24454)
        result_b = pm.auto_create_tunnel_mods("srv-b", str(dir_b), voicechat_port=24454)

    assert result_a["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    assert result_b["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    assert api.created == 1
    assert "t-legacy" not in store
    for d in (dir_a, dir_b):
        cfg = load_playit_config(d)
        assert cfg["voicechat_tunnel_id"] == "t-new-1"
        assert cfg["voicechat_endpoint"] == "new.tun.ply.gg:7800"


def _sync_pm(tmp_path):

    pm = _auto_create_pm(tmp_path)
    return pm


def _sync_cfg(tmp_path, name, **overrides):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    cfg = {
        "voicechat_port": 24454,
        "bedrock_port": 19132,
        "java_endpoint": "",
        "bedrock_endpoint": "",
        "voicechat_endpoint": "",
        "java_tunnel_id": "",
        "bedrock_tunnel_id": "",
        "voicechat_tunnel_id": "",
    }
    cfg.update(overrides)
    _save_voice_cfg(d, **cfg)
    return d


def _sync_run(pm, api, servers):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        return pm.sync_account_tunnels(servers)


def test_sync_migrates_shared_legacy_once(tmp_path):
    """Two servers sharing one legacy voice tunnel: single replacement,
    both configs rewritten, map recorded."""
    import json

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _sync_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(tmp_path, "srvA", voicechat_tunnel_id="t-legacy", voicechat_endpoint="old.tun.ply.gg:1111")
    dir_b = _sync_cfg(tmp_path, "srvB", voicechat_tunnel_id="t-legacy", voicechat_endpoint="old.tun.ply.gg:1111")

    summary = _sync_run(pm, api, [("a", str(dir_a)), ("b", str(dir_b))])

    assert summary["status"] == "ok"
    assert api.created == 1
    assert "t-legacy" not in store
    assert store["t-new-1"]["tunnel_type"] is None
    assert len(summary["migrated"]) == 1
    assert sorted(summary["migrated"][0]["servers"]) == ["a", "b"]
    for d in (dir_a, dir_b):
        cfg = load_playit_config(d)
        assert cfg["voicechat_tunnel_id"] == "t-new-1"
        assert cfg["voicechat_endpoint"] == "new.tun.ply.gg:7800"
    mmap = json.loads((tmp_path / "playit" / ".voice-migrated.json").read_text(encoding="utf-8"))
    assert mmap["t-legacy"]["id"] == "t-new-1"


def test_sync_deletes_unreferenced_legacy_voice_tunnel(tmp_path):
    pm = _sync_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(tmp_path, "srvA")

    summary = _sync_run(pm, api, [("a", str(dir_a))])

    assert summary["cleaned"] == ["t-legacy"]
    assert "t-legacy" not in store
    assert api.created == 0


def test_sync_keeps_bedrock_wired_tunnel(tmp_path):
    """A voice-named tunnel referenced as a bedrock endpoint is manual
    wiring, not migration material: hands off."""
    pm = _sync_pm(tmp_path)
    store = {
        "t-weird": _tunnel_data(
            "t-weird",
            local_port=19132,
            name="hosty-voicechat-udp-19132-1",
            tunnel_type="minecraft-bedrock",
            domain="w.tun.ply.gg",
            remote_port=2222,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(tmp_path, "srvA", bedrock_endpoint="w.tun.ply.gg:2222", bedrock_port=19132)

    summary = _sync_run(pm, api, [("a", str(dir_a))])

    assert summary["migrated"] == []
    assert summary["cleaned"] == []
    assert "t-weird" in store
    assert api.created == 0


def test_sync_adopts_empty_refs_onto_existing_tunnels(tmp_path):
    """A new server with no tunnel config automatically shows an
    already-created same-port Java tunnel instead of duplicating it.
    Bedrock/voice need their setup process, so they are left alone."""
    from hosty.shared.backend.playit_config import load_playit_config

    pm = _sync_pm(tmp_path)
    store = {
        "t-java": _tunnel_data(
            "t-java",
            proto="tcp",
            tunnel_type="minecraft-java",
            local_port=25565,
            domain="j.tun.ply.gg",
            remote_port=3333,
            name="hosty-srv-tcp-25565-1",
        ),
        "t-bed": _tunnel_data(
            "t-bed",
            local_port=19132,
            name="hosty-bedrock-udp-19132-1",
            tunnel_type="minecraft-bedrock",
            domain="b.tun.ply.gg",
            remote_port=4444,
        ),
        "t-voice": _tunnel_data(
            "t-voice",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type=None,
            domain="v.tun.ply.gg",
            remote_port=5555,
        ),
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(tmp_path, "srvA")
    (dir_a / "server.properties").write_text("server-port=25565\n", encoding="utf-8")

    summary = _sync_run(pm, api, [("a", str(dir_a))])

    assert api.created == 0
    cfg = load_playit_config(dir_a)
    assert cfg["java_tunnel_id"] == "t-java"
    assert cfg["java_endpoint"] == "j.tun.ply.gg"
    assert cfg["bedrock_tunnel_id"] == ""
    assert cfg["bedrock_endpoint"] == ""
    assert cfg["voicechat_tunnel_id"] == ""
    assert cfg["voicechat_endpoint"] == ""
    assert len(summary["adopted"]) == 1
    assert summary["adopted"][0]["kind"] == "java"


def test_sync_backfills_id_from_live_endpoint(tmp_path):
    from hosty.shared.backend.playit_config import load_playit_config

    pm = _sync_pm(tmp_path)
    store = {
        "t-voice": _tunnel_data(
            "t-voice",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type=None,
            domain="v.tun.ply.gg",
            remote_port=5555,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(tmp_path, "srvA", voicechat_endpoint="v.tun.ply.gg:5555")

    summary = _sync_run(pm, api, [("a", str(dir_a))])

    assert load_playit_config(dir_a)["voicechat_tunnel_id"] == "t-voice"
    assert api.created == 0
    assert len(summary["healed"]) == 1


def test_sync_respects_tombstone(tmp_path):
    """A dead stored id with no live match only clears a stale endpoint;
    nothing is adopted or created in its place."""
    from hosty.shared.backend.playit_config import load_playit_config

    pm = _sync_pm(tmp_path)
    store = {
        "t-other": _tunnel_data(
            "t-other",
            local_port=24454,
            name="hosty-voicechat-udp-24454-9",
            tunnel_type=None,
            domain="o.tun.ply.gg",
            remote_port=6666,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(
        tmp_path,
        "srvA",
        voicechat_tunnel_id="t-deleted",
        voicechat_endpoint="gone.tun.ply.gg:7777",
    )

    summary = _sync_run(pm, api, [("a", str(dir_a))])

    cfg = load_playit_config(dir_a)
    assert cfg["voicechat_endpoint"] == ""
    assert cfg["voicechat_tunnel_id"] == "t-deleted"
    assert api.created == 0
    assert summary["adopted"] == []


def test_sync_aborts_on_stale_list(tmp_path):
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = _sync_pm(tmp_path)
    dir_a = _sync_cfg(tmp_path, "srvA", voicechat_endpoint="v.tun.ply.gg:5555")

    with patch.object(PlayitManager, "_request", side_effect=RuntimeError("offline")):
        summary = pm.sync_account_tunnels([("a", str(dir_a))])

    assert summary["status"] == "stale"
    from hosty.shared.backend.playit_config import load_playit_config

    assert load_playit_config(dir_a)["voicechat_endpoint"] == "v.tun.ply.gg:5555"


def test_sync_repairs_java_ref_pointing_at_voice_tunnel(tmp_path):
    """The reported corruption: java id+endpoint point at the live raw voice
    tunnel while a valid java tunnel exists. Sync must forget the corrupt
    ref and adopt the real java tunnel."""
    from hosty.shared.backend.playit_config import load_playit_config

    pm = _sync_pm(tmp_path)
    store = {
        "t-java": _tunnel_data(
            "t-java",
            proto="tcp",
            tunnel_type="minecraft-java",
            local_port=25565,
            domain="j.tun.ply.gg",
            remote_port=3333,
            name="hosty-server-tcp-25565-1",
        ),
        "t-voice": _tunnel_data(
            "t-voice",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type=None,
            domain="v.tun.ply.gg",
            remote_port=5555,
        ),
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(
        tmp_path,
        "srvA",
        java_tunnel_id="t-voice",
        java_endpoint="v.tun.ply.gg:5555",
        voicechat_tunnel_id="t-voice",
        voicechat_endpoint="v.tun.ply.gg:5555",
    )
    (dir_a / "server.properties").write_text("server-port=25565\n", encoding="utf-8")

    _sync_run(pm, api, [("a", str(dir_a))])

    cfg = load_playit_config(dir_a)
    assert cfg["java_tunnel_id"] == "t-java"
    assert cfg["java_endpoint"] == "j.tun.ply.gg"
    assert cfg["voicechat_tunnel_id"] == "t-voice"
    assert cfg["voicechat_endpoint"] == "v.tun.ply.gg:5555"
    assert api.created == 0


def test_sync_forgets_bedrock_ref_pointing_at_tcp_tunnel(tmp_path):
    """Same class of corruption for bedrock: the ref is dropped, and with no
    setup-process adoption the fields stay empty for the normal flows."""
    from hosty.shared.backend.playit_config import load_playit_config

    pm = _sync_pm(tmp_path)
    store = {
        "t-java": _tunnel_data(
            "t-java",
            proto="tcp",
            tunnel_type="minecraft-java",
            local_port=25565,
            domain="j.tun.ply.gg",
            remote_port=3333,
            name="hosty-server-tcp-25565-1",
        ),
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(
        tmp_path,
        "srvA",
        bedrock_tunnel_id="t-java",
        bedrock_endpoint="j.tun.ply.gg:3333",
    )

    _sync_run(pm, api, [("a", str(dir_a))])

    cfg = load_playit_config(dir_a)
    assert cfg["bedrock_tunnel_id"] == ""
    assert cfg["bedrock_endpoint"] == ""


def test_auto_create_clears_java_endpoint_on_udp_tunnel(tmp_path):
    """Validation must not treat a live-but-wrong-protocol match as valid."""
    from contextlib import ExitStack

    from hosty.shared.backend.playit_config import load_playit_config

    pm = _auto_create_pm(tmp_path)
    store = {
        "t-voice": _tunnel_data(
            "t-voice",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type=None,
            domain="v.tun.ply.gg",
            remote_port=5555,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    _save_voice_cfg(
        tmp_path,
        java_endpoint="v.tun.ply.gg:5555",
    )

    with ExitStack() as stack:
        for ctx in _alloc_patches(pm, api):
            stack.enter_context(ctx)
        pm.auto_create_tunnel_mods("srv", str(tmp_path), voicechat_port=24454)

    assert load_playit_config(tmp_path)["java_endpoint"] == ""


def test_tunnel_cache_file_is_human_readable(tmp_path):
    from hosty.shared.backend.playit_manager import PlayitManager

    cache = PlayitManager.TunnelCacheHelper(tmp_path)
    cache.add_tunnel("t-1", {"name": "hosty-x", "origin": {"data": {"local_port": 1}}})
    content = (tmp_path / "tunnel-cache.json").read_text(encoding="utf-8")
    assert "\n" in content
    assert '"t-1": {' in content


def _parser_pm(tmp_path):
    from hosty.shared.backend.playit_manager import PlayitManager

    pm = PlayitManager()
    pm._agent_id = "agent-1"
    pm.emit_on_main_thread = lambda *args, **kwargs: None
    return pm


def _prime_tunnels(pm, store):
    from hosty.shared.backend.playit_manager import PlayitManager

    pm.tunnels = {"tcp": [], "udp": [], "both": []}
    for tunnel_id, data in store.items():
        pm.tunnels["udp" if data.get("port_type") == "udp" else "tcp"].append(PlayitManager.Tunnel(pm, data))
    pm.tunnels_refreshed_at = 1.0


def test_log_parser_never_assigns_voice_candidate_to_java_slot(tmp_path):
    """Agent logs contain UDP voice domain:port lines; the fallback must not
    put them into a server's (java) active endpoint."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = _parser_pm(tmp_path)
    _prime_tunnels(
        pm,
        {
            "t-voice": _tunnel_data(
                "t-voice",
                local_port=24454,
                name="hosty-voicechat-udp-24454-1",
                tunnel_type=None,
                domain="v.tun.ply.gg",
                remote_port=5555,
            ),
        },
    )
    pm._active_server_ids = {"s1": {"tunnel_id": None, "endpoint": "", "port": None, "server_dir": str(tmp_path)}}

    with patch.object(PlayitManager, "_retrieve_tunnels", lambda self: self.tunnels):
        pm._parse_line_for_endpoints("tunnel running v.tun.ply.gg:5555 -> 127.0.0.1:24454")

    assert pm._active_server_ids["s1"]["endpoint"] == ""


def test_log_parser_assigns_matching_tcp_candidate(tmp_path):
    """A TCP candidate on the server's java port still fills the slot."""
    from unittest.mock import patch

    from hosty.shared.backend.playit_manager import PlayitManager

    pm = _parser_pm(tmp_path)
    _prime_tunnels(
        pm,
        {
            "t-java": _tunnel_data(
                "t-java",
                proto="tcp",
                tunnel_type="minecraft-java",
                local_port=25565,
                domain="j.tun.ply.gg",
                remote_port=3333,
                name="hosty-srv-tcp-25565-1",
            ),
        },
    )
    pm._active_server_ids = {"s1": {"tunnel_id": None, "endpoint": "", "port": None, "server_dir": str(tmp_path)}}
    (tmp_path / "server.properties").write_text("server-port=25565\n", encoding="utf-8")

    with patch.object(PlayitManager, "_retrieve_tunnels", lambda self: self.tunnels):
        pm._parse_line_for_endpoints("tunnel running j.tun.ply.gg:3333 -> 127.0.0.1:25565")

    assert pm._active_server_ids["s1"]["endpoint"] == "j.tun.ply.gg:3333"


def test_sync_migration_leaves_active_java_slot_alone(tmp_path):
    """Voice migration must never touch _active_server_ids (the java slot):
    previously it wrote the voice endpoint there, and the UI then copied it
    over the server's java_endpoint on every refresh."""

    pm = _sync_pm(tmp_path)
    store = {
        "t-legacy": _tunnel_data(
            "t-legacy",
            local_port=24454,
            name="hosty-voicechat-udp-24454-1",
            tunnel_type="minecraft-bedrock",
            domain="old.tun.ply.gg",
            remote_port=1111,
        )
    }
    api = _FakePlayitApi(store, allowed=4)
    dir_a = _sync_cfg(
        tmp_path,
        "srvA",
        voicechat_tunnel_id="t-legacy",
        voicechat_endpoint="old.tun.ply.gg:1111",
    )
    pm._active_server_ids = {"a": {"tunnel_id": None, "endpoint": "", "port": None, "server_dir": str(dir_a)}}

    _sync_run(pm, api, [("a", str(dir_a))])

    assert pm._active_server_ids["a"]["endpoint"] == ""
    assert pm._active_server_ids["a"]["tunnel_id"] is None
