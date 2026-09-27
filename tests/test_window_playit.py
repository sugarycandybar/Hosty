"""Tests for the playit agent keep-alive/start gating in HostyWindow.

Uses the real unbound method with a stubbed self (no GTK instantiation).
"""

import time
import types

from hosty.gtk_ui.window import HostyWindow


class _StubPlayit:
    def __init__(self):
        self.start_calls = []

    def is_running_for(self, server_id):
        return False

    def start(self, server_id, server_dir, secret="", auto_install=True):
        self.start_calls.append(server_id)
        return True, "playit started"

    def auto_create_tunnel_mods(self, *args, **kwargs):
        return {}

    def verify_playit_mod_configs(self, *args, **kwargs):
        return {}


class _StubInfo:
    loader_type = "fabric"

    def __init__(self, server_dir):
        self.server_dir = server_dir


def _fake_window(tmp_path, playit, cfg, manual_stop=False):
    fake = types.SimpleNamespace()
    fake._server_manager = types.SimpleNamespace(
        playit_manager=playit,
        get_server=lambda sid: _StubInfo(str(tmp_path)),
    )
    fake._playit_autostart_paused_ids = set()
    fake._playit_starting_server_ids = set()
    fake._playit_agent_manual_stop = manual_stop
    fake._load_playit_config = lambda sid: dict(cfg)
    fake.clear_playit_auto_start_pause = types.MethodType(HostyWindow.clear_playit_auto_start_pause, fake)

    def sync_playit_tunnels(self):
        fake.sync_playit_tunnels_called = True
        return {}

    fake._server_manager.sync_playit_tunnels = sync_playit_tunnels
    return fake


def _base_cfg(**overrides):
    cfg = {
        "enabled": True,
        "auto_start": True,
        "secret": "s",
        "auto_install": True,
        "bedrock_port": 19132,
        "voicechat_port": 24454,
    }
    cfg.update(overrides)
    return cfg


def _wait_for(calls, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    return list(calls)


def test_keep_alive_respects_manual_stop(tmp_path):
    playit = _StubPlayit()
    fake = _fake_window(tmp_path, playit, _base_cfg(), manual_stop=True)

    HostyWindow._apply_playit_runtime(fake, "s1", None)
    time.sleep(0.3)

    assert playit.start_calls == []
    assert fake._playit_agent_manual_stop is True


def test_fresh_server_start_clears_manual_stop_and_starts(tmp_path):
    playit = _StubPlayit()
    fake = _fake_window(tmp_path, playit, _base_cfg(), manual_stop=True)

    HostyWindow._apply_playit_runtime(fake, "s1", "start")

    assert _wait_for(playit.start_calls) == ["s1"]
    assert fake._playit_agent_manual_stop is False


def test_fresh_start_with_playit_disabled_keeps_manual_stop(tmp_path):
    playit = _StubPlayit()
    fake = _fake_window(tmp_path, playit, _base_cfg(enabled=False), manual_stop=True)

    HostyWindow._apply_playit_runtime(fake, "s1", "start")
    time.sleep(0.3)

    assert playit.start_calls == []
    assert fake._playit_agent_manual_stop is True


def test_keep_alive_starts_when_nothing_paused(tmp_path):
    playit = _StubPlayit()
    fake = _fake_window(tmp_path, playit, _base_cfg(), manual_stop=False)

    HostyWindow._apply_playit_runtime(fake, "s1", None)

    assert _wait_for(playit.start_calls) == ["s1"]
