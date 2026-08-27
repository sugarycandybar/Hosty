"""
Real-world integration test: full server lifecycle like a user would do it.

Creates servers for every loader, simulates installs with dummy jars
(no network / no real Minecraft download), verifies launch commands,
mod/plugin dirs, persistence, and cleanup. Mirrors the GUI flows
(Create -> list -> Properties -> Files -> Start -> Stop -> Delete)
but exercised via ServerManager + ServerProcess directly.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hosty.shared.utils import constants as c

# ---------------------------------------------------------------------------
# Helpers to simulate a successful install without hitting the network
# ---------------------------------------------------------------------------


def _fake_install_fabric(server_dir: Path):
    (server_dir / "fabric-server-launch.jar").write_text("fake fabric")
    (server_dir / "server.jar").write_text("fake vanilla")


def _fake_install_paper(server_dir: Path, build: str = "119"):
    (server_dir / "paper-server.jar").write_text(f"fake paper build {build}")


def _fake_install_neoforge(server_dir: Path, version: str = "26.2.0.67"):
    ver_dir = server_dir / "libraries" / "net" / "neoforged" / "neoforge" / version
    ver_dir.mkdir(parents=True)
    (ver_dir / "unix_args.txt").write_text("-p neoforge")
    (server_dir / "user_jvm_args.txt").write_text("# -Xmx2G\n")


def _fake_install_forge(server_dir: Path, mc_version: str = "1.21.4", build: str = "53.0.1"):
    ver_dir = server_dir / "libraries" / "net" / "minecraftforge" / "forge" / f"{mc_version}-{build}"
    ver_dir.mkdir(parents=True)
    (ver_dir / "unix_args.txt").write_text("-p forge")
    (server_dir / "user_jvm_args.txt").write_text("# jvm args\n")


# ---------------------------------------------------------------------------
# The real-world flow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "loader, mc_version, installer",
    [
        ("fabric", "1.21.4", _fake_install_fabric),
        ("paper", "26.2", lambda d: _fake_install_paper(d, "119")),
        ("neoforge", "26.2", lambda d: _fake_install_neoforge(d, "26.2.0.67")),
        ("forge", "1.21.4", lambda d: _fake_install_forge(d, "1.21.4", "53.0.1")),
    ],
)
def test_create_configure_launch_cycle(server_manager, loader, mc_version, installer):
    """Create a server, simulate install, verify launch, mutate, delete."""
    mgr = server_manager

    # --- Create (like CreateServerDialog) ---
    info = mgr.add_server(
        name=f"Test {loader}",
        mc_version=mc_version,
        loader_type=loader,
        loader_version="test-build",
        java_version=c.get_required_java_version(mc_version),
    )
    assert info.loader_type == loader
    assert info.mc_version == mc_version
    assert info.server_dir.exists()

    # Persistence round-trip (servers.json)
    from hosty.shared.backend.server_manager import ServerInfo

    raw = info.to_dict()
    assert ServerInfo(raw).loader_type == loader

    # --- Simulate install ---
    installer(info.server_dir)

    # --- Launch command (like ServerProcess.start) ---
    proc = mgr.get_process(info.id)
    assert proc is not None
    args, err = proc._build_launch_command()
    assert err == "", err
    assert args is not None
    assert args[-1] == "nogui"
    if loader == "fabric":
        assert "fabric-server-launch.jar" in args[1]
    elif loader == "paper":
        assert "paper-server.jar" in args[1]
    else:
        assert any(a.startswith("@") for a in args)

    # --- Files: mods vs plugins dir ---
    expected_dir = "plugins" if loader == "paper" else "mods"
    assert c.content_dir_name(loader) == expected_dir
    content_dir = info.server_dir / expected_dir
    content_dir.mkdir(exist_ok=True)
    (content_dir / "dummy.jar").write_text("x")
    assert (content_dir / "dummy.jar").exists()

    # --- Properties: mutate RAM / JVM args / Java version ---
    mgr.update_server_ram(info.id, 4096)
    assert mgr.get_server(info.id).ram_mb == 4096

    # Java version change should sync to cached process
    new_java = 25 if info.java_version != 25 else 21
    info.java_version = new_java
    mgr._save()
    mgr.refresh_process_runtime(info.id)
    assert mgr.get_existing_process(info.id).jvm_args == info.jvm_args

    # --- Delete (like sidebar context menu) ---
    server_dir = info.server_dir
    mgr.delete_server(info.id, delete_files=True)
    assert mgr.get_server(info.id) is None
    assert not server_dir.exists()


def test_java_version_insufficient_logic():
    """Mirrors the yellow-highlight logic in the UI."""
    assert c.get_required_java_version("26.2") == 25
    # Java 21 is insufficient for 26.2 -> should be yellow
    assert 21 < c.get_required_java_version("26.2")
    assert not (25 < c.get_required_java_version("26.2"))


def test_modrinth_search_routing_for_paper():
    """Paper searches should include plugin project type."""

    from hosty.shared.backend import modrinth_client

    captured_facets = {}

    def fake_request(req, **kw):
        # urlopen is called with a urllib.request.Request
        url = req.full_url if hasattr(req, "full_url") else str(req)
        # Capture the facets query param
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(url).query)
        facets = qs.get("facets", [""])[0]
        captured_facets["facets"] = facets

        class FakeResp:
            def read(self):
                return b'{"hits": [], "total_hits": 0}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return FakeResp()

    with patch("hosty.shared.backend.modrinth_client.urllib.request.urlopen", side_effect=fake_request):
        with patch("hosty.shared.backend.modrinth_client.make_ssl_context", return_value=None):
            modrinth_client.search_mods("test", loader="paper")

    assert "project_type:plugin" in captured_facets["facets"]
    assert "categories:paper" in captured_facets["facets"]


def test_update_runtime_keeps_loader(server_manager, tmp_path: Path):
    """Updating MC version must not change loader type."""
    mgr = server_manager
    info = mgr.add_server(name="Neo", mc_version="1.21.4", loader_type="neoforge", loader_version="21.4.10")

    # Simulate an update: change MC version, keep same loader
    # Mock the heavy parts (java download, installer) - just test the metadata path
    with patch.object(mgr.download_manager, "download_installer", return_value="/tmp/fake.jar"):
        with patch.object(mgr.download_manager, "install_server", return_value=(True, "ok")):
            with patch.object(mgr, "create_full_backup", return_value=(True, "ok")):
                with patch.object(
                    mgr, "scan_update_compatibility", return_value={"compatible": {}, "incompatible": {}, "unknown": {}}
                ):
                    with patch.object(mgr, "apply_compatible_component_updates", return_value=(0, 0)):
                        with patch.object(mgr, "isolate_incompatible_components", return_value={}):
                            ok, msg = mgr.update_server_runtime(info.id, "26.2", loader_version="26.2.0.67")
                            # May fail on java download if not mocked fully, but loader must stay
                            assert mgr.get_server(info.id).loader_type == "neoforge"
