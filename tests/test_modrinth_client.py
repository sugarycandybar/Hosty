"""Offline tests for Modrinth client fast paths (no network).

Covers server-side version filtering with identical-result fallbacks,
the response cache, and the pooled-connection fallback. The HTTP layer is
fully stubbed; only client-side selection logic is exercised.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse

import pytest

from hosty.shared.backend import modrinth_client


def _raw_version(
    version_id: str,
    project_id: str,
    version_number: str,
    game_versions: list[str],
    loaders: list[str],
) -> dict:
    return {
        "id": version_id,
        "project_id": project_id,
        "title": f"Title {version_number}",
        "name": f"Name {version_number}",
        "version_number": version_number,
        "game_versions": list(game_versions),
        "loaders": list(loaders),
        "date_published": "2026-01-01T00:00:00Z",
        "files": [
            {
                "primary": True,
                "url": f"https://cdn.example/{version_id}.jar",
                "filename": f"{version_id}.jar",
                "hashes": {},
            }
        ],
    }


VERSIONS = [
    _raw_version("v-new-fabric", "demo", "2.0", ["1.21.1"], ["fabric"]),
    _raw_version("v-old-fabric", "demo", "1.0", ["1.20.1"], ["fabric"]),
    _raw_version("v-new-paper", "demo", "2.0", ["1.21.1"], ["paper"]),
    _raw_version("v-noloader", "demo", "3.0", ["1.21.1"], []),
    _raw_version("v-dp", "demo", "9.9", ["1.21.1"], ["datapack"]),
]


class FakeTransport:
    """Stub for _request_json_pooled/_request_json_direct."""

    def __init__(self, versions, honor_filters: bool = True, fail_pooled: bool = False):
        self.versions = versions
        self.honor_filters = honor_filters
        self.fail_pooled = fail_pooled
        self.pooled_calls: list[str] = []
        self.direct_calls: list[str] = []

    def _serve(self, url: str):
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        result = list(self.versions)
        if self.honor_filters:
            if "loaders" in params:
                wanted = {x.lower() for x in json.loads(params["loaders"][0])}
                result = [v for v in result if wanted & {x.lower() for x in v["loaders"]}]
            if "game_versions" in params:
                wanted = set(json.loads(params["game_versions"][0]))
                result = [v for v in result if wanted & set(v["game_versions"])]
        return result

    def pooled(self, url: str, timeout: float = 30.0):
        self.pooled_calls.append(url)
        if self.fail_pooled:
            raise ConnectionError("stale pooled connection")
        return self._serve(url)

    def direct(self, url: str, timeout: float = 30.0):
        self.direct_calls.append(url)
        return self._serve(url)


@pytest.fixture()
def transport(monkeypatch: pytest.MonkeyPatch):
    modrinth_client.clear_cache()
    fake = FakeTransport(VERSIONS)
    monkeypatch.setattr(modrinth_client, "_request_json_pooled", fake.pooled)
    monkeypatch.setattr(modrinth_client, "_request_json_direct", fake.direct)
    yield fake
    modrinth_client.clear_cache()


def test_exact_match_uses_single_filtered_request(transport: FakeTransport):
    best = modrinth_client.find_compatible_version("demo", "1.21.1", loader="fabric")
    assert best is not None
    assert best.version_id == "v-new-fabric"
    assert len(transport.pooled_calls) == 1
    assert not transport.direct_calls
    assert "loaders" in transport.pooled_calls[0]
    assert "game_versions" in transport.pooled_calls[0]


def test_loader_fallback_when_no_mc_match(transport: FakeTransport):
    # No fabric build for 1.19: falls back to newest loader-only match.
    best = modrinth_client.find_compatible_version("demo", "1.19.4", loader="fabric")
    assert best is not None
    assert best.version_id == "v-new-fabric"
    assert len(transport.pooled_calls) == 2


def test_unfiltered_fallback_for_unknown_loader(transport: FakeTransport):
    best = modrinth_client.find_compatible_version("demo", "1.21.1", loader="quilt")
    assert best is not None
    assert best.version_id == "v-new-fabric"


def test_datapack_loader_matches_datapack_versions(transport: FakeTransport):
    best = modrinth_client.find_compatible_version("demo", "1.21.1", loader="datapack")
    assert best is not None
    assert best.version_id == "v-dp"
    assert len(transport.pooled_calls) == 1
    assert "loaders" in transport.pooled_calls[0]


def test_datapack_loader_falls_back_without_mc_match(transport: FakeTransport):
    best = modrinth_client.find_compatible_version("demo", "1.19.4", loader="datapack")
    assert best is not None
    assert best.version_id == "v-dp"


def test_plugin_loader_uses_plugin_matcher(transport: FakeTransport):
    best = modrinth_client.find_compatible_version("demo", "1.21.1", loader="plugin")
    assert best is not None
    assert best.version_id == "v-new-paper"


def _raw_version_to_model(version_id: str, loaders: list[str]):
    raw = _raw_version(version_id, "demo", "1.0", ["1.21.1"], loaders)
    model = modrinth_client._version_to_model(raw)
    assert model is not None
    return model


def test_is_datapack_version():
    assert modrinth_client.is_datapack_version(_raw_version_to_model("x", ["datapack"])) is True
    assert modrinth_client.is_datapack_version(_raw_version_to_model("x", [])) is True
    assert modrinth_client.is_datapack_version(_raw_version_to_model("x", ["fabric"])) is False


def test_results_identical_without_server_filter_support(monkeypatch: pytest.MonkeyPatch):
    modrinth_client.clear_cache()
    fake = FakeTransport(VERSIONS, honor_filters=False)
    monkeypatch.setattr(modrinth_client, "_request_json_pooled", fake.pooled)
    monkeypatch.setattr(modrinth_client, "_request_json_direct", fake.direct)
    try:
        best = modrinth_client.find_compatible_version("demo", "1.21.1", loader="fabric")
        assert best is not None
        assert best.version_id == "v-new-fabric"
        plugin = modrinth_client.find_compatible_plugin_version("demo", "1.21.1")
        assert plugin is not None
        assert plugin.version_id == "v-new-paper"
    finally:
        modrinth_client.clear_cache()


def test_version_list_cache_avoids_refetch(transport: FakeTransport):
    modrinth_client.get_project_versions("demo")
    modrinth_client.get_project_versions("demo")
    assert len(transport.pooled_calls) == 1


def test_cache_expiry_refetches(transport: FakeTransport, monkeypatch: pytest.MonkeyPatch):
    modrinth_client.get_project_versions("demo")
    assert len(transport.pooled_calls) == 1
    monkeypatch.setattr(modrinth_client, "_CACHE_TTL", -1.0)
    modrinth_client.get_project_versions("demo")
    assert len(transport.pooled_calls) == 2


def test_filtered_request_failure_retries_unfiltered(monkeypatch: pytest.MonkeyPatch):
    modrinth_client.clear_cache()
    calls: list[str] = []

    def failing(url: str, timeout: float = 30.0):
        calls.append(url)
        if "loaders" in url or "game_versions" in url:
            raise urllib.error.HTTPError(url, 400, "bad request", {}, None)
        return list(VERSIONS)

    monkeypatch.setattr(modrinth_client, "_request_json_pooled", failing)
    monkeypatch.setattr(modrinth_client, "_request_json_direct", failing)
    try:
        versions = modrinth_client.get_project_versions("demo", loaders=["fabric"])
        assert [v.version_id for v in versions] == [v["id"] for v in VERSIONS]
        assert calls and "loaders" not in calls[-1]
    finally:
        modrinth_client.clear_cache()


def test_pooled_failure_falls_back_to_direct(transport: FakeTransport):
    transport.fail_pooled = True
    best = modrinth_client.find_compatible_version("demo", "1.21.1", loader="fabric")
    assert best is not None
    assert best.version_id == "v-new-fabric"
    assert transport.direct_calls
