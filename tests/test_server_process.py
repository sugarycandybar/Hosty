"""Tests for ServerProcess launch-command generation."""

from pathlib import Path

from hosty.shared.backend.server_process import ServerProcess


def _proc(tmp_path: Path) -> ServerProcess:
    return ServerProcess(server_dir=str(tmp_path), java_path="/usr/bin/java", ram_mb=2048)


def test_fabric_launch(tmp_path: Path):
    p = _proc(tmp_path)
    (tmp_path / "fabric-server-launch.jar").write_text("x")
    args, err = p._build_launch_command()
    assert err == ""
    assert args == ["-jar", "fabric-server-launch.jar", "nogui"]


def test_paper_launch(tmp_path: Path):
    p = _proc(tmp_path)
    (tmp_path / "paper-server.jar").write_text("x")
    args, err = p._build_launch_command()
    assert args == ["-jar", "paper-server.jar", "nogui"]


def test_fabric_takes_precedence_over_paper(tmp_path: Path):
    p = _proc(tmp_path)
    (tmp_path / "fabric-server-launch.jar").write_text("x")
    (tmp_path / "paper-server.jar").write_text("x")
    args, _ = p._build_launch_command()
    assert args[1] == "fabric-server-launch.jar"


def test_forge_via_libraries(tmp_path: Path):
    p = _proc(tmp_path)
    ver_dir = tmp_path / "libraries" / "net" / "minecraftforge" / "forge" / "1.20.1-47.1.0"
    ver_dir.mkdir(parents=True)
    (ver_dir / "unix_args.txt").write_text("-p foo")
    (tmp_path / "user_jvm_args.txt").write_text("-Xmx2G")
    args, err = p._build_launch_command()
    assert err == ""
    assert args[0].startswith("@")
    assert any("unix_args.txt" in a for a in args)
    assert args[-1] == "nogui"


def test_neoforge_via_libraries(tmp_path: Path):
    p = _proc(tmp_path)
    ver_dir = tmp_path / "libraries" / "net" / "neoforged" / "neoforge" / "21.4.157"
    ver_dir.mkdir(parents=True)
    (ver_dir / "unix_args.txt").write_text("-p neo")
    args, err = p._build_launch_command()
    assert err == ""
    assert any("unix_args.txt" in a for a in args)


def test_missing_launch_config_reports_all_loaders(tmp_path: Path):
    p = _proc(tmp_path)
    args, err = p._build_launch_command()
    assert args is None
    assert "fabric-server-launch.jar" in err
    assert "paper-server.jar" in err
    assert "libraries" in err
