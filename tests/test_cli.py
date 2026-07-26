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
    assert "0.4.0" in result.output


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


def test_add_with_ignore_and_check_now():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            result = invoke(["add", "https://x.test", "--name", "t1", "--ignore", r"\d+ views", "--check-now"], home)
        assert result.exit_code == 0
        assert "Baseline captured" in result.output
        w = storage.get_watch("t1")
        assert w["ignore_patterns"] == [r"\d+ views"]
        assert w["last_hash"] is not None


def test_add_rejects_invalid_ignore_regex():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["add", "https://x.test", "--name", "t1", "--ignore", "[bad"], home)
        assert result.exit_code == 1
        assert "invalid regex" in result.output


def test_update_watch_fields_and_baseline_reset():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1", "--interval", "60"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)
        assert storage.get_watch("t1")["last_hash"] is not None

        result = invoke(["update", "t1", "--interval", "120"], home)
        assert result.exit_code == 0
        w = storage.get_watch("t1")
        assert w["interval"] == 120
        assert w["last_hash"] is not None  # interval change keeps the baseline

        result = invoke(["update", "t1", "--add-ignore", r"updated at \d"], home)
        assert result.exit_code == 0
        w = storage.get_watch("t1")
        assert w["ignore_patterns"] == [r"updated at \d"]
        assert w["last_hash"] is None  # content-affecting change resets baseline

        result = invoke(["update", "t1", "--remove-ignore", r"updated at \d"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t1")["ignore_patterns"] == []

        result = invoke(["update", "t1", "--add-ignore", "[bad"], home)
        assert result.exit_code == 1

        result = invoke(["update", "missing", "--interval", "60"], home)
        assert result.exit_code == 1

        result = invoke(["update", "t1"], home)
        assert result.exit_code == 0
        assert "Nothing to update" in result.output


def test_config_set_and_validation():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))

        assert invoke(["config", "set", "interval", "600"], home).exit_code == 0
        assert storage.load_config()["interval"] == 600

        assert invoke(["config", "set", "proxy", "http://127.0.0.1:7890"], home).exit_code == 0
        assert storage.load_config()["proxy"] == "http://127.0.0.1:7890"
        assert invoke(["config", "set", "proxy", "none"], home).exit_code == 0
        assert storage.load_config()["proxy"] is None

        assert invoke(["config", "set", "retries", "5"], home).exit_code == 0
        assert storage.load_config()["retries"] == 5
        assert invoke(["config", "set", "retries", "11"], home).exit_code == 1
        assert invoke(["config", "set", "interval", "abc"], home).exit_code == 1
        assert invoke(["config", "set", "nope", "1"], home).exit_code == 1

        # Bare 'config' still prints the settings.
        result = invoke(["config"], home)
        assert result.exit_code == 0
        assert "interval" in result.output


def test_history_command():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)

        result = invoke(["history", "t1"], home)
        assert result.exit_code == 0
        assert "No snapshot history" in result.output

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2))):
            invoke(["check", "--no-alerts"], home)

        result = invoke(["history", "t1"], home)
        assert result.exit_code == 0
        assert "current" in result.output
        assert "previous" in result.output

        result = invoke(["history", "t1", "--limit", "1"], home)
        assert result.exit_code == 0
        assert "showing 1 of 2" in result.output

        result = invoke(["history", "missing"], home)
        assert result.exit_code == 1


def test_check_json_output_and_fail_on_change():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            result = invoke(["check", "--json", "--no-alerts"], home)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["results"][0]["name"] == "t1"
        assert data["results"][0]["changed"] is False
        assert data["alerts"] == []

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2))):
            result = invoke(["check", "--json", "--no-alerts", "--fail-on-change"], home)
        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["results"][0]["changed"] is True

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2))):
            result = invoke(["check", "--fail-on-change", "--no-alerts"], home)
        assert result.exit_code == 0  # no change on the repeat check


def test_import_merge_and_replace():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as home2:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2))):
            invoke(["check", "--no-alerts"], home)

        backup = Path(home) / "backup.json"
        assert invoke(["export", "-o", str(backup)], home).exit_code == 0

        # Import into a fresh home: watch + history restored.
        result = invoke(["import", str(backup)], home2)
        assert result.exit_code == 0
        assert "Imported 1 watch(es)" in result.output
        storage2 = Storage(Path(home2))
        assert storage2.get_watch("t1") is not None
        assert len(storage2.load_snapshot("t1")["history"]) == 2

        # Merging again skips the existing name.
        result = invoke(["import", str(backup)], home2)
        assert "skipped 1 existing" in result.output

        # Replace mode swaps the watch list entirely.
        invoke(["add", "https://y.test", "--name", "t2"], home2)
        result = invoke(["import", str(backup), "--replace"], home2)
        assert result.exit_code == 0
        storage2 = Storage(Path(home2))
        assert storage2.get_watch("t2") is None
        assert storage2.get_watch("t1") is not None

        # Corrupt backup fails cleanly.
        bad = Path(home2) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert invoke(["import", str(bad)], home2).exit_code == 1
        bad.write_text('{"nothing": true}', encoding="utf-8")
        assert invoke(["import", str(bad)], home2).exit_code == 1
