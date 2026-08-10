"""Install/uninstall pagewatch as a per-user system service (systemd/launchd)."""
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .utils import data_dir

SYSTEMD_UNIT_NAME = "pagewatch.service"
LAUNCHD_LABEL = "tech.pagewatch.daemon"


class ServiceError(RuntimeError):
    """Raised when service installation/uninstallation cannot proceed."""


def _default_data_dir() -> Path:
    return Path.home() / ".pagewatch"


def resolve_exec_path() -> str:
    """Prefer the installed console script; fall back to 'python -m pagewatch'."""
    exe = shutil.which("pagewatch")
    if exe:
        return exe
    return f"{sys.executable} -m pagewatch"


def generate_systemd_unit(exec_path: str, data_dir_path: Path | str) -> str:
    lines = [
        "[Unit]",
        "Description=PageWatch website change monitor",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={exec_path} watch",
        "Restart=on-failure",
        "RestartSec=30",
    ]
    data_dir_path = Path(data_dir_path).expanduser()
    if data_dir_path != _default_data_dir():
        lines.append(f"Environment=PAGEWATCH_HOME={data_dir_path}")
    lines += [
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def generate_launchd_plist(exec_path: str, data_dir_path: Path | str) -> str:
    data_dir_path = Path(data_dir_path).expanduser()
    args = [escape(arg) for arg in (*shlex.split(exec_path), "watch")]
    program_args = "\n".join(f"        <string>{arg}</string>" for arg in args)
    env_block = ""
    if data_dir_path != _default_data_dir():
        env_block = (
            "    <key>EnvironmentVariables</key>\n"
            "    <dict>\n"
            "        <key>PAGEWATCH_HOME</key>\n"
            f"        <string>{escape(str(data_dir_path))}</string>\n"
            "    </dict>\n"
        )
    log_path = escape(str(data_dir_path / "pagewatch.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>
{env_block}    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def _checked_run(run, cmd: list[str]) -> None:
    try:
        run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise ServiceError(f"'{cmd[0]}' not found — cannot manage the service on this system.") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"command failed: {' '.join(cmd)}"
        if detail:
            message += f"\n{detail}"
        raise ServiceError(message) from None


def _unsupported(platform: str) -> ServiceError:
    hint = ""
    if platform.startswith(("win", "cygwin", "msys")):
        hint = (
            " On Windows, use Task Scheduler instead, e.g.:\n"
            "  schtasks /Create /SC HOURLY /TN PageWatch "
            f"/TR \"{resolve_exec_path()} watch\""
        )
    return ServiceError(f"Automatic service installation is not supported on this platform ({platform}).{hint}")


def install_service(exec_path: str | None = None, data_dir_path: Path | str | None = None,
                    *, platform: str | None = None, home: Path | None = None, run=None) -> Path:
    """Write the user service unit and enable it. Returns the unit file path."""
    platform = platform or sys.platform
    home = home or Path.home()
    run = run or subprocess.run
    supported = platform.startswith("linux") or platform == "darwin"
    if not supported:
        raise _unsupported(platform)
    exec_path = exec_path or resolve_exec_path()
    data_dir_path = Path(data_dir_path) if data_dir_path else data_dir()

    if platform.startswith("linux"):
        target = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate_systemd_unit(exec_path, data_dir_path), encoding="utf-8")
        _checked_run(run, ["systemctl", "--user", "daemon-reload"])
        _checked_run(run, ["systemctl", "--user", "enable", "--now", "pagewatch"])
        return target
    if platform == "darwin":
        target = home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate_launchd_plist(exec_path, data_dir_path), encoding="utf-8")
        _checked_run(run, ["launchctl", "load", str(target)])
        return target
    raise AssertionError("unreachable")  # unsupported platforms raise above


def uninstall_service(*, platform: str | None = None, home: Path | None = None, run=None) -> Path:
    """Disable the user service and remove its unit file. Returns the removed path."""
    platform = platform or sys.platform
    home = home or Path.home()
    run = run or subprocess.run

    if platform.startswith("linux"):
        target = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
        if not target.is_file():
            raise ServiceError(f"Service is not installed (no {target}).")
        _checked_run(run, ["systemctl", "--user", "disable", "--now", "pagewatch"])
        target.unlink()
        _checked_run(run, ["systemctl", "--user", "daemon-reload"])
        return target
    if platform == "darwin":
        target = home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if not target.is_file():
            raise ServiceError(f"Service is not installed (no {target}).")
        _checked_run(run, ["launchctl", "unload", str(target)])
        target.unlink()
        return target
    raise _unsupported(platform)
