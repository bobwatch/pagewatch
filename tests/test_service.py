import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pagewatch.service import (
    ServiceError,
    generate_launchd_plist,
    generate_systemd_unit,
    install_service,
    resolve_exec_path,
    uninstall_service,
)


class FakeRun:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, cmd, check=False, capture_output=False, text=False):
        self.calls.append(list(cmd))
        if self.fail_on and self.fail_on in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")


def test_generate_systemd_unit_contents():
    unit = generate_systemd_unit("/usr/local/bin/pagewatch", Path.home() / ".pagewatch")
    assert "ExecStart=/usr/local/bin/pagewatch watch" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit
    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit
    # Default data dir needs no PAGEWATCH_HOME override.
    assert "PAGEWATCH_HOME" not in unit


def test_generate_systemd_unit_custom_data_dir_sets_env():
    custom = Path("/srv/pagewatch-data")
    unit = generate_systemd_unit("/usr/bin/python -m pagewatch", custom)
    assert "ExecStart=/usr/bin/python -m pagewatch watch" in unit
    assert f"Environment=PAGEWATCH_HOME={custom}" in unit


def test_generate_launchd_plist_contents():
    plist = generate_launchd_plist("/usr/local/bin/pagewatch", Path.home() / ".pagewatch")
    assert "<string>tech.pagewatch.daemon</string>" in plist
    assert "<key>RunAtLoad</key>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert "<string>/usr/local/bin/pagewatch</string>" in plist
    assert "<string>watch</string>" in plist
    assert "PAGEWATCH_HOME" not in plist


def test_generate_launchd_plist_custom_data_dir_sets_env():
    custom = Path("/srv/pagewatch-data")
    plist = generate_launchd_plist("/usr/bin/python -m pagewatch", custom)
    assert "<key>PAGEWATCH_HOME</key>" in plist
    assert f"<string>{custom}</string>" in plist
    # "python -m pagewatch" must be split into separate program arguments.
    assert "<string>-m</string>" in plist
    assert "<string>pagewatch</string>" in plist


def test_install_service_linux_writes_unit_and_enables():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        run = FakeRun()
        target = install_service(exec_path="/usr/bin/pagewatch", data_dir_path=home / "data",
                                 platform="linux", home=home, run=run)
        assert target == home / ".config" / "systemd" / "user" / "pagewatch.service"
        unit = target.read_text(encoding="utf-8")
        assert "ExecStart=/usr/bin/pagewatch watch" in unit
        assert f"Environment=PAGEWATCH_HOME={home / 'data'}" in unit
        assert run.calls == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "pagewatch"],
        ]


def test_install_service_macos_writes_plist_and_loads():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        run = FakeRun()
        target = install_service(exec_path="/usr/bin/pagewatch", data_dir_path=home / "data",
                                 platform="darwin", home=home, run=run)
        assert target == home / "Library" / "LaunchAgents" / "tech.pagewatch.daemon.plist"
        assert "tech.pagewatch.daemon" in target.read_text(encoding="utf-8")
        assert run.calls == [["launchctl", "load", str(target)]]


def test_install_service_unsupported_platform():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ServiceError, match="not supported"):
            install_service(platform="win32", home=Path(tmp), run=FakeRun())
        # Nothing may be written on unsupported platforms.
        assert list(Path(tmp).iterdir()) == []


def test_install_service_windows_hint_mentions_task_scheduler():
    with pytest.raises(ServiceError, match="schtasks"):
        install_service(platform="win32", home=Path.home(), run=FakeRun())


def test_install_service_systemctl_failure_is_clear_error():
    with tempfile.TemporaryDirectory() as tmp:
        run = FakeRun(fail_on="enable")
        with pytest.raises(ServiceError, match="boom"):
            install_service(exec_path="/usr/bin/pagewatch", data_dir_path=Path(tmp),
                            platform="linux", home=Path(tmp), run=run)


def test_uninstall_service_linux_disables_and_removes():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        target = home / ".config" / "systemd" / "user" / "pagewatch.service"
        target.parent.mkdir(parents=True)
        target.write_text("unit", encoding="utf-8")
        run = FakeRun()
        removed = uninstall_service(platform="linux", home=home, run=run)
        assert removed == target
        assert not target.exists()
        assert run.calls == [
            ["systemctl", "--user", "disable", "--now", "pagewatch"],
            ["systemctl", "--user", "daemon-reload"],
        ]


def test_uninstall_service_not_installed():
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(ServiceError, match="not installed"):
        uninstall_service(platform="linux", home=Path(tmp), run=FakeRun())


def test_resolve_exec_path_falls_back_to_python_m():
    assert resolve_exec_path()
    # The fallback form must be runnable as a module invocation.
    if shutil.which("pagewatch") is None:
        assert resolve_exec_path() == f"{sys.executable} -m pagewatch"


def test_python_m_pagewatch_help():
    src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    proc = subprocess.run([sys.executable, "-m", "pagewatch", "--help"],
                          capture_output=True, text=True, env=env, timeout=60, check=False)
    assert proc.returncode == 0
    assert "Usage" in proc.stdout
