"""
Modrinth API v2 — search and download Fabric mods (stdlib only).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

USER_AGENT = "Hosty/1.0 (+https://github.com/hosty)"
API = "https://api.modrinth.com/v2"


@dataclass
class ModrinthHit:
    project_id: str
    slug: str
    title: str
    description: str
    icon_url: Optional[str]
    latest_version: str
    downloads: int
    author: str
    categories: list[str]


@dataclass
class ModrinthVersion:
    version_id: str
    name: str
    version_number: str
    game_versions: list[str]
    loaders: list[str]
    published: str
    download_url: str
    filename: str


def _request_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_primary_file(files: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    primary = next((f for f in files if f.get("primary")), None)
    if primary:
        return primary
    jar = next((f for f in files if str(f.get("filename", "")).endswith(".jar")), None)
    return jar if jar else (files[0] if files else None)


def search_mods(
    query: str,
    limit: int = 20,
    offset: int = 0,
    sort: str = "relevance",
    game_version: Optional[str] = None,
    category: Optional[str] = None,
    loader: str = "fabric",
    server_side_only: bool = True,
) -> tuple[list[ModrinthHit], int]:
    """Search Modrinth with optional filters and pagination.

    Returns (hits, total_hits).
    """
    qtext = query.strip() or "fabric"
    base = {
        "query": qtext,
        "limit": str(max(1, limit)),
        "offset": str(max(0, offset)),
        "index": sort or "relevance",
    }
    facets_raw: list[list[str]] = [["project_type:mod"], [f"categories:{loader}"]]
    if server_side_only:
        facets_raw.append(["server_side:required", "server_side:optional"])
    if category:
        facets_raw.append([f"categories:{category}"])
    if game_version:
        facets_raw.append([f"versions:{game_version}"])
    facets = json.dumps(facets_raw)
    url = f"{API}/search?{urllib.parse.urlencode({**base, 'facets': facets})}"
    try:
        data = _request_json(url)
    except urllib.error.HTTPError:
        url = f"{API}/search?{urllib.parse.urlencode(base)}"
        data = _request_json(url)
    hits: list[ModrinthHit] = []
    for h in data.get("hits", []):
        hits.append(
            ModrinthHit(
                project_id=h["project_id"],
                slug=h.get("slug", ""),
                title=h.get("title", h["project_id"]),
                description=(h.get("description") or "")[:280],
                icon_url=h.get("icon_url"),
                latest_version=h.get("latest_version", ""),
                downloads=int(h.get("downloads") or 0),
                author=h.get("author", ""),
                categories=[str(c) for c in (h.get("categories") or [])],
            )
        )
    total_hits = int(data.get("total_hits") or len(hits))
    return hits, total_hits


def get_project_versions(project_id: str) -> list[ModrinthVersion]:
    """Return all available versions for a project (newest first per API)."""
    url = f"{API}/project/{project_id}/version"
    try:
        raw_versions = _request_json(url)
    except urllib.error.HTTPError:
        return []

    out: list[ModrinthVersion] = []
    for ver in raw_versions:
        files = ver.get("files") or []
        chosen = _pick_primary_file(files)
        if not chosen:
            continue
        out.append(
            ModrinthVersion(
                version_id=ver.get("id", ""),
                name=ver.get("name", ""),
                version_number=ver.get("version_number", ""),
                game_versions=[str(v) for v in (ver.get("game_versions") or [])],
                loaders=[str(v) for v in (ver.get("loaders") or [])],
                published=ver.get("date_published", ""),
                download_url=chosen.get("url", ""),
                filename=chosen.get("filename", "mod.jar"),
            )
        )
    return out


def find_compatible_versions(
    project_id: str,
    game_version: str,
    loader: str = "fabric",
    limit: int = 8,
) -> list[ModrinthVersion]:
    """Return compatible versions, preferring exact MC+loader, then loader only."""
    all_versions = get_project_versions(project_id)
    if not all_versions:
        return []

    loader_l = loader.lower()
    exact = [
        v
        for v in all_versions
        if game_version in v.game_versions
        and loader_l in [x.lower() for x in v.loaders]
    ]
    if exact:
        return exact[:limit]

    loader_only = [
        v for v in all_versions if loader_l in [x.lower() for x in v.loaders]
    ]
    if loader_only:
        return loader_only[:limit]

    return all_versions[:1]


def find_compatible_version(
    project_id: str,
    game_version: str,
    loader: str = "fabric",
) -> Optional[ModrinthVersion]:
    """Return best single version for install, or None."""
    versions = find_compatible_versions(project_id, game_version, loader=loader, limit=1)
    return versions[0] if versions else None


def find_compatible_version_file(
    project_id: str, game_version: str, loader: str = "fabric"
) -> Optional[tuple[str, str]]:
    """
    Returns (download_url, filename) for the best-matching version file, or None.
    """
    chosen = find_compatible_version(project_id, game_version, loader=loader)
    if not chosen:
        return None
    return (chosen.download_url, chosen.filename)


def download_to(url: str, dest: Path, timeout: float = 120.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.write_bytes(data)
