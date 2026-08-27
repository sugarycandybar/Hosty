"""Tests for DownloadManager loader resolution (mocked network + live smoke)."""

from unittest.mock import MagicMock, patch

import pytest

from hosty.shared.backend.download_manager import DownloadManager

# ---------------------------------------------------------------------------
# Mocked tests - deterministic, no network
# ---------------------------------------------------------------------------


def _mock_resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.raise_for_status = MagicMock()
    return m


def test_resolve_fabric_uses_first_loader(monkeypatch):
    dm = DownloadManager()
    monkeypatch.setattr(dm, "fetch_loader_versions", lambda: ["0.16.9", "0.15.0"])
    assert dm.resolve_loader_build("fabric", "1.21.4") == "0.16.9"


def test_resolve_forge_prefers_recommended(monkeypatch):
    dm = DownloadManager()
    promos = {"1.21.4-recommended": "53.0.1", "1.21.4-latest": "53.0.9"}
    with patch("hosty.shared.backend.download_manager.requests.get", return_value=_mock_resp({"promos": promos})):
        assert dm.resolve_loader_build("forge", "1.21.4") == "53.0.1"


def test_resolve_forge_falls_back_to_latest(monkeypatch):
    dm = DownloadManager()
    promos = {"1.21.4-latest": "53.0.9"}
    with patch("hosty.shared.backend.download_manager.requests.get", return_value=_mock_resp({"promos": promos})):
        assert dm.resolve_loader_build("forge", "1.21.4") == "53.0.9"


def test_resolve_neoforge_branch_matching(monkeypatch):
    dm = DownloadManager()
    versions = ["21.4.0", "21.4.10", "21.4.157", "21.11.45", "21.0.5"]
    with patch("hosty.shared.backend.download_manager.requests.get", return_value=_mock_resp({"versions": versions})):
        assert dm.resolve_loader_build("neoforge", "1.21.4") == "21.4.157"
        # 1.21 should NOT pick 21.11.x (segment-wise matching)
        assert dm.resolve_loader_build("neoforge", "1.21") == "21.0.5"
        assert dm.resolve_loader_build("neoforge", "1.20.1") == ""


def test_resolve_neoforge_new_scheme(monkeypatch):
    dm = DownloadManager()
    versions = ["26.2.0.60", "26.2.0.67", "26.1.2.97", "26.1.1.15-beta"]
    with patch("hosty.shared.backend.download_manager.requests.get", return_value=_mock_resp({"versions": versions})):
        assert dm.resolve_loader_build("neoforge", "26.2") == "26.2.0.67"
        assert dm.resolve_loader_build("neoforge", "26.1.2") == "26.1.2.97"
        # beta-only branch falls back to beta
        assert dm.resolve_loader_build("neoforge", "26.1.1") == "26.1.1.15-beta"


def test_resolve_paper_prefers_stable(monkeypatch):
    dm = DownloadManager()

    builds = [
        {"id": 118, "channel": "STABLE"},
        {"id": 119, "channel": "EXPERIMENTAL"},
        {"id": 117, "channel": "STABLE"},
    ]

    def fake_get(url, **kw):
        if url.endswith("/builds"):
            return _mock_resp(builds)
        if "latest" in url:
            return _mock_resp({"id": 118, "downloads": {"server:default": {"url": "x"}}})
        raise AssertionError(f"unexpected url {url}")

    with patch("hosty.shared.backend.download_manager.requests.get", side_effect=fake_get):
        assert dm.resolve_loader_build("paper", "26.2") == "118"  # newest STABLE (118 > 117)


def test_resolve_paper_fallback_to_latest_when_no_stable(monkeypatch):
    dm = DownloadManager()
    builds = [{"id": 5, "channel": "EXPERIMENTAL"}]
    latest = {"id": 5, "downloads": {"server:default": {"url": "x"}}}

    def fake_get(url, **kw):
        if url.endswith("/builds"):
            return _mock_resp(builds)
        return _mock_resp(latest)

    with patch("hosty.shared.backend.download_manager.requests.get", side_effect=fake_get):
        # No STABLE -> falls back to /latest
        with patch.object(dm, "_fetch_paper_build", return_value=latest):
            assert dm.resolve_loader_build("paper", "26.2") == "5"


def test_session_cache(monkeypatch):
    dm = DownloadManager()
    calls = {"n": 0}

    def counting_fetch(*a, **kw):
        calls["n"] += 1
        return _mock_resp({"promos": {"1.21.4-recommended": "53.0.1"}})

    with patch("hosty.shared.backend.download_manager.requests.get", side_effect=counting_fetch):
        assert dm.resolve_loader_build("forge", "1.21.4") == "53.0.1"
        assert dm.resolve_loader_build("forge", "1.21.4") == "53.0.1"
        assert calls["n"] == 1  # second hit served from cache


# ---------------------------------------------------------------------------
# Live smoke tests - hit real APIs, skipped if offline
# ---------------------------------------------------------------------------


@pytest.mark.network
def test_live_neoforge_26_2():
    dm = DownloadManager()
    build = dm.resolve_loader_build("neoforge", "26.2")
    assert build.startswith("26.2.0.")


@pytest.mark.network
def test_live_paper_26_2():
    dm = DownloadManager()
    build = dm.resolve_loader_build("paper", "26.2")
    assert build.isdigit()
    assert int(build) > 0
