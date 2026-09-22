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
    store = {"t-voice": _tunnel_data("t-voice", local_port=24454, name="hosty-voicechat-udp-24454-1")}
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
    store = {"t-voice": _tunnel_data("t-voice", local_port=24454, name="hosty-voicechat-udp-24454-9")}
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

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": ""}
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

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": ""}
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
    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": "new.tun.ply.gg:7800"}
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

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": ""}
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

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": ""}
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

    assert result == {"bedrock_endpoint": "", "voicechat_endpoint": ""}
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
