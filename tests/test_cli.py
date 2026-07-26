import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

import pagewatch.cli as cli_module
from pagewatch.alerts import AlertManager
from pagewatch.cli import cli
from pagewatch.monitor import Monitor
from pagewatch.storage import Storage

PAGE_V1 = "<html><body><p>version one</p></body></html>"
PAGE_V2 = "<html><body><p>version two</p></body></html>"


class StaticFetcher:
    def __init__(self, html):
        self.html = html

    def __call__(self, url):
        return self.html, url


class FakeResponse:
    status_code = 200


class FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()


def invoke(args, home):
    return CliRunner().invoke(cli, args, env={"PAGEWATCH_HOME": home})


@contextmanager
def patched(monitor=None, alert_manager=None):
    orig_monitor = cli_module.get_monitor
    orig_alerts = cli_module.get_alert_manager
    if monitor is not None:
        cli_module.get_monitor = lambda: monitor
    if alert_manager is not None:
        cli_module.get_alert_manager = lambda: alert_manager
    try:
        yield
    finally:
        cli_module.get_monitor = orig_monitor
        cli_module.get_alert_manager = orig_alerts


def test_version_reports_current_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.output


def test_add_list_remove_watch():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["add", "example.com", "--name", "t1", "--interval", "60"], home)
        assert result.exit_code == 0
        assert "Added watch" in result.output

        result = invoke(["list"], home)
        assert "t1" in result.output

        result = invoke(["add", "example.com", "--name", "t1"], home)
        assert result.exit_code == 1

        result = invoke(["remove", "t1"], home)
        assert result.exit_code == 0
        result = invoke(["remove", "t1"], home)
        assert result.exit_code == 1


def test_add_rejects_invalid_url():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["add", "https://"], home)
        assert result.exit_code == 1


def test_alert_channel_management():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["alert", "add", "https://hooks.example/x", "--name", "ops", "--format", "slack"], home)
        assert result.exit_code == 0
        assert "ops" in result.output

        result = invoke(["alert", "add", "https://hooks.example/y", "--name", "ops"], home)
        assert result.exit_code == 1

        result = invoke(["alert", "list"], home)
        assert "ops" in result.output
        assert "slack" in result.output

        result = invoke(["alert", "remove", "ops"], home)
        assert result.exit_code == 0
        result = invoke(["alert", "remove", "ops"], home)
        assert result.exit_code == 1


def test_alert_test_without_channels_is_graceful():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["alert", "test"], home)
        assert result.exit_code == 0
        assert "No alert channels" in result.output


def test_check_detects_change_and_dispatches_alerts():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        session = FakeSession()
        alert_manager = AlertManager(storage=storage, session=session)

        invoke(["add", "https://x.test", "--name", "t1"], home)
        alert_manager.add_channel("https://hooks.example/x", name="ops", fmt="slack", events="change")

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1)), alert_manager=alert_manager):
            result = invoke(["check"], home)
        assert result.exit_code == 0
        assert session.posts == []  # first check is baseline, no change

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2)), alert_manager=alert_manager):
            result = invoke(["check"], home)
        assert result.exit_code == 0
        assert "YES" in result.output
        assert "Alert sent" in result.output
        assert len(session.posts) == 1
        assert "change detected" in session.posts[0]["json"]["text"]

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1)), alert_manager=alert_manager):
            result = invoke(["check", "--no-alerts"], home)
        assert result.exit_code == 0
        assert len(session.posts) == 1  # unchanged: --no-alerts suppressed dispatch


def test_watch_once_runs_single_pass():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            result = invoke(["watch", "--once", "--no-alerts"], home)
        assert result.exit_code == 0
        assert "no change" in result.output


def test_export_json_and_csv():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2))):
            invoke(["check", "--no-alerts"], home)

        result = invoke(["export"], home)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pagewatch_version"]
        assert len(data["watches"]) == 1
        snap = data["snapshots"]["t1"]
        assert "html" not in snap["latest"]
        assert snap["previous"]["full_text"] == "version one"
        assert len(snap["history"]) == 2

        result = invoke(["export", "--include-html"], home)
        data = json.loads(result.output)
        assert data["snapshots"]["t1"]["latest"]["html"] == PAGE_V2

        result = invoke(["export", "--format", "csv"], home)
        lines = [ln for ln in result.output.strip().splitlines() if ln]
        assert lines[0].startswith("name,url,timestamp")
        assert len(lines) == 3  # header + two history rows


def test_diff_command_shows_previous_vs_latest():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)

        result = invoke(["diff", "t1"], home)
        assert result.exit_code == 0
        assert "No diff possible yet" in result.output

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2))):
            invoke(["check", "--no-alerts"], home)

        result = invoke(["diff", "t1"], home)
        assert result.exit_code == 0
        assert "version two" in result.output

        result = invoke(["diff", "missing"], home)
        assert result.exit_code == 1


def test_config_outputs_settings():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["config"], home)
        assert result.exit_code == 0
        assert "interval" in result.output
