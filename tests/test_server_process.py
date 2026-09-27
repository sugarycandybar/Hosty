"""Tests for ServerProcess launch-command generation."""

import subprocess
import threading
from pathlib import Path

from hosty.shared.backend.server_process import ServerProcess
from hosty.shared.utils.constants import ServerStatus


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


"""Race-guard tests: stop/start interleavings must never orphan or kill processes."""


class _FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)

    def flush(self):
        pass


class _FakeStdout:
    def readline(self):
        return ""


class _FakeProc:
    """Stand-in for subprocess.Popen with controllable wait()."""

    def __init__(self, pid=111, block=None, hang=False):
        self.pid = pid
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()
        self.kill_calls = 0
        self._block = block or threading.Event()
        self._hang = hang
        if block is None:
            self._block.set()

    def wait(self, timeout=None):
        if self._hang:
            raise subprocess.TimeoutExpired("fake", timeout)
        if not self._block.wait(5):
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def kill(self):
        self.kill_calls += 1

    def poll(self):
        return None


def _quiet(proc):
    proc.emit_on_main_thread = lambda *args, **kwargs: None
    return proc


def _proc_with(proc_obj, status=ServerStatus.RUNNING, epoch=1, pid=111, tmp_path=None):
    import tempfile

    p = ServerProcess(server_dir=str(tmp_path or tempfile.mkdtemp()), java_path="/usr/bin/java")
    _quiet(p)
    p._process = proc_obj
    p._status = status
    p._epoch = epoch
    p._pid = pid
    return p


def test_is_running_includes_stopping(tmp_path):
    p = _proc_with(_FakeProc(), status=ServerStatus.STOPPING, tmp_path=tmp_path)
    assert p.is_running is True
    p._status = ServerStatus.STOPPED
    assert p.is_running is False


def test_start_refused_while_stopping(tmp_path):
    p = _proc_with(_FakeProc(), status=ServerStatus.STOPPING, tmp_path=tmp_path)
    assert p.start() is False
    assert p._process is not None  # old handle untouched


def test_stop_is_idempotent_while_stopping(tmp_path):
    gate = threading.Event()
    proc = _FakeProc(block=gate)
    p = _proc_with(proc, status=ServerStatus.RUNNING, tmp_path=tmp_path)
    p.stop()
    assert p.status == ServerStatus.STOPPING
    p.stop()  # second stop must not send another command
    assert len(proc.stdin.writes) == 1
    gate.set()
    p._stop_thread.join(timeout=5)
    assert p.status == ServerStatus.STOPPED


def test_stale_stop_waiter_ignores_replacement(tmp_path):
    gate = threading.Event()
    old = _FakeProc(block=gate)
    new = _FakeProc()
    p = _proc_with(old, status=ServerStatus.RUNNING, tmp_path=tmp_path)
    p.stop()
    # A newer start takes over before the old waiter finishes.
    p._epoch = 2
    p._process = new
    p._status = ServerStatus.RUNNING
    p._pid = 222
    gate.set()
    p._stop_thread.join(timeout=5)
    assert p.status == ServerStatus.RUNNING
    assert p._process is new
    assert p._pid == 222
    assert new.kill_calls == 0


def test_stale_stop_waiter_kills_old_but_spares_replacement(tmp_path):
    gate = threading.Event()
    old = _FakeProc(block=gate, hang=True)
    new = _FakeProc()
    p = _proc_with(old, status=ServerStatus.RUNNING, tmp_path=tmp_path)
    p.stop()
    p._epoch = 2
    p._process = new
    p._status = ServerStatus.RUNNING
    p._pid = 222
    p._stop_thread.join(timeout=5)
    assert old.kill_calls == 1  # hanging old process is still reaped
    assert new.kill_calls == 0
    assert p.status == ServerStatus.RUNNING
    assert p._process is new


def test_reader_finally_ignores_stale_epoch(tmp_path):
    old = _FakeProc()
    new = _FakeProc()
    p = _proc_with(new, status=ServerStatus.RUNNING, epoch=2, pid=222, tmp_path=tmp_path)
    p._read_output(1, old)  # stale reader drains EOF
    assert p.status == ServerStatus.RUNNING
    assert p._process is new
    assert p._pid == 222


def test_reader_finally_stops_current_process(tmp_path):
    proc = _FakeProc()
    p = _proc_with(proc, status=ServerStatus.RUNNING, epoch=3, pid=333, tmp_path=tmp_path)
    p._read_output(3, proc)
    assert p.status == ServerStatus.STOPPED
    assert p._pid is None
