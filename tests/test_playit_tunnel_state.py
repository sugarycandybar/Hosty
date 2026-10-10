"""Offline tests for PlayitManager.stored_tunnel_state (no network)."""

from __future__ import annotations

import time
import types

from hosty.shared.backend.playit_manager import PlayitManager


def _tunnel(
    tunnel_id="t1",
    protocol="tcp",
    raw_tunnel_type=None,
    hostname="abc.tun.ply.gg",
    domain="",
    remote_port=None,
):
    return types.SimpleNamespace(
        id=tunnel_id,
        protocol=protocol,
        raw_tunnel_type=raw_tunnel_type,
        hostname=hostname,
        domain=domain,
        remote_port=remote_port,
    )


def _manager_with(tunnels):
    mgr = PlayitManager.__new__(PlayitManager)
    mgr.tunnels = {"tcp": [], "udp": [], "both": []}
    for tunnel in tunnels:
        bucket = tunnel.protocol if tunnel.protocol in mgr.tunnels else "tcp"
        mgr.tunnels[bucket].append(tunnel)
    mgr.tunnels_refreshed_at = time.monotonic()
    return mgr


def test_empty_reference_is_none():
    mgr = PlayitManager.__new__(PlayitManager)
    mgr.tunnels = {"tcp": [], "udp": [], "both": []}
    mgr.tunnels_refreshed_at = None
    assert mgr.stored_tunnel_state("java", "", "") == "none"


def test_stale_list_is_unknown():
    mgr = _manager_with([])
    mgr.tunnels_refreshed_at = time.monotonic() - 3600
    assert mgr.stored_tunnel_state("java", "dead-id", "") == "unknown"
    assert mgr.stored_tunnel_state("java", "", "gone.tun.ply.gg") == "unknown"


def test_live_by_id_and_endpoint():
    mgr = _manager_with([_tunnel("t1")])
    assert mgr.stored_tunnel_state("java", "t1", "") == "live"
    assert mgr.stored_tunnel_state("java", "", "abc.tun.ply.gg") == "live"
    assert mgr.stored_tunnel_state("java", "t1", "abc.tun.ply.gg") == "live"


def test_dead_id_is_missing():
    mgr = _manager_with([_tunnel("t1")])
    assert mgr.stored_tunnel_state("java", "dead-id", "") == "missing"


def test_endpoint_without_live_match_is_missing():
    mgr = _manager_with([_tunnel("t1")])
    assert mgr.stored_tunnel_state("java", "", "gone.tun.ply.gg") == "missing"


def test_wrong_kind_is_missing():
    mgr = _manager_with([_tunnel("t1", protocol="udp", raw_tunnel_type="minecraft-bedrock")])
    # A java (tcp) reference pointing at a bedrock tunnel is corruption.
    assert mgr.stored_tunnel_state("java", "t1", "") == "missing"
    assert mgr.stored_tunnel_state("bedrock", "t1", "") == "live"


def test_unrefreshed_list_is_unknown():
    mgr = PlayitManager.__new__(PlayitManager)
    mgr.tunnels = {"tcp": [], "udp": [], "both": []}
    mgr.tunnels_refreshed_at = None
    assert mgr.stored_tunnel_state("java", "t1", "") == "unknown"
