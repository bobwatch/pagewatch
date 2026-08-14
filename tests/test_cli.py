import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from xml.dom import minidom

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

    def __call__(self, url, **kwargs):
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
    assert "0.5.0" in result.output


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
        # No webhooks and no email configured: friendly hint, clean exit.
        assert result.exit_code == 0
        assert "No alert channels configured" in result.output


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
        assert "distinct" in result.output and "diff" in result.output

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


def test_add_render_flag_is_stored():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        result = invoke(["add", "https://x.test", "--name", "t1", "--render"], home)
        assert result.exit_code == 0
        assert "JS" in result.output
        assert storage.get_watch("t1")["render"] is True

        result = invoke(["add", "https://y.test", "--name", "t2"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t2")["render"] is False


def test_update_render_toggle_resets_baseline():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)
        assert storage.get_watch("t1")["last_hash"] is not None

        result = invoke(["update", "t1", "--render"], home)
        assert result.exit_code == 0
        assert "Baseline reset" in result.output
        w = storage.get_watch("t1")
        assert w["render"] is True
        assert w["last_hash"] is None  # rendered content differs hugely — old baseline is invalid

        # Re-applying the same value is not a toggle: baseline stays.
        rendered_monitor = Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1),
                                   render_fetcher=StaticFetcher(PAGE_V1))
        with patched(monitor=rendered_monitor):
            invoke(["check", "--no-alerts"], home)
        result = invoke(["update", "t1", "--render"], home)
        assert result.exit_code == 0
        assert "Baseline reset" not in result.output
        assert storage.get_watch("t1")["last_hash"] is not None

        result = invoke(["update", "t1", "--no-render"], home)
        assert result.exit_code == 0
        assert "Baseline reset" in result.output
        w = storage.get_watch("t1")
        assert w["render"] is False
        assert w["last_hash"] is None


def test_clone_preserves_render():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1", "--render"], home)
        result = invoke(["clone", "t1", "--new-name", "t2"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t2")["render"] is True


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


def test_pause_and_resume_watch():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        assert storage.get_watch("t1")["paused"] is False

        result = invoke(["pause", "t1"], home)
        assert result.exit_code == 0
        assert "Paused" in result.output
        assert storage.get_watch("t1")["paused"] is True

        result = invoke(["pause", "missing"], home)
        assert result.exit_code == 1

        result = invoke(["resume", "t1"], home)
        assert result.exit_code == 0
        assert "Resumed" in result.output
        assert storage.get_watch("t1")["paused"] is False

        result = invoke(["resume", "missing"], home)
        assert result.exit_code == 1

        # Paused watch should be skipped during check
        storage.update_watch("t1", paused=True)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            result = invoke(["check", "--no-alerts"], home)
        assert result.exit_code == 0


def test_list_shows_paused_status():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        storage.update_watch("t1", paused=True)
        result = invoke(["list"], home)
        assert "paus" in result.output.lower()  # Rich table may truncate "paused"


def test_alert_email_config_commands():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["alert", "email", "show"], home)
        assert result.exit_code == 0
        assert "not configured" in result.output.lower()

        result = invoke([
            "alert", "email", "set",
            "--smtp-host", "smtp.test.com",
            "--smtp-port", "587",
            "--smtp-user", "user@test.com",
            "--smtp-pass", "secret",
            "--to-addrs", "alerts@test.com",
        ], home)
        assert result.exit_code == 0
        assert "saved" in result.output.lower()

        result = invoke(["alert", "email", "show"], home)
        assert "smtp.test.com" in result.output


def test_alert_update_channel():
    with tempfile.TemporaryDirectory() as home:
        invoke(["alert", "add", "https://hooks.example/a", "--name", "ops", "--format", "slack"], home)
        result = invoke(["alert", "update", "ops", "--format", "discord", "--events", "all"], home)
        assert result.exit_code == 0
        assert "discord" in result.output
        assert "all" in result.output

        result = invoke(["alert", "update", "nope", "--format", "discord"], home)
        assert result.exit_code == 1


def test_alert_add_new_formats_roundtrip():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["alert", "add", "https://api.telegram.org/botT/sendMessage?chat_id=1",
                         "--name", "tg", "--format", "telegram"], home)
        assert result.exit_code == 0
        assert "telegram" in result.output

        result = invoke(["alert", "list"], home)
        assert "telegram" in result.output

        result = invoke(["alert", "add", "https://ntfy.sh/mytopic", "--name", "nt", "--format", "ntfy"], home)
        assert result.exit_code == 0

        result = invoke(["alert", "add", "https://hooks.example/x", "--format", "nope"], home)
        assert result.exit_code != 0


def test_add_rejects_non_positive_interval():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        result = invoke(["add", "https://x.test", "--name", "t1", "--interval", "-5"], home)
        assert result.exit_code == 1
        assert "positive" in result.output

        result = invoke(["add", "https://x.test", "--name", "t1", "--interval", "0"], home)
        assert result.exit_code == 1
        assert storage.get_watch("t1") is None


def test_add_rejects_invalid_selector():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        result = invoke(["add", "https://x.test", "--name", "t1", "--selector", "p["], home)
        assert result.exit_code == 1
        assert "selector" in result.output.lower()
        assert storage.get_watch("t1") is None

        result = invoke(["add", "https://x.test", "--name", "t1", "--selector", "#main"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t1")["selector"] == "#main"


def test_add_uses_config_interval_as_default():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        assert invoke(["config", "set", "interval", "900"], home).exit_code == 0

        result = invoke(["add", "https://x.test", "--name", "t1"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t1")["interval"] == 900

        # An explicit --interval still wins over the config default.
        result = invoke(["add", "https://y.test", "--name", "t2", "--interval", "60"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t2")["interval"] == 60


def test_add_generates_safe_name_for_port_url():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        result = invoke(["add", "http://example.com:8080/path"], home)
        assert result.exit_code == 0
        watch = storage.get_watch("example-com-8080")
        assert watch is not None

        # The generated name must be usable for snapshot writes (Windows-safe).
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            result = invoke(["check", "--name", "example-com-8080", "--no-alerts"], home)
        assert result.exit_code == 0
        assert storage.load_snapshot("example-com-8080") is not None


def test_update_interval_and_pause_apply_together():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)

        result = invoke(["update", "t1", "--interval", "300", "--pause"], home)
        assert result.exit_code == 0
        watch = storage.get_watch("t1")
        assert watch["interval"] == 300
        assert watch["paused"] is True
        assert "paused" in result.output


def test_update_selector_and_clear_selector_conflict():
    with tempfile.TemporaryDirectory() as home:
        invoke(["add", "https://x.test", "--name", "t1"], home)
        result = invoke(["update", "t1", "--selector", "#a", "--clear-selector"], home)
        assert result.exit_code == 1
        assert "cannot be used together" in result.output


def test_alert_add_and_list_mask_webhook_url():
    with tempfile.TemporaryDirectory() as home:
        url = "https://hooks.slack.com/services/T00/B00/secret-token"
        result = invoke(["alert", "add", url, "--name", "ops"], home)
        assert result.exit_code == 0
        assert "secret-token" not in result.output
        assert "https://hooks.slack.com" in result.output

        result = invoke(["alert", "list"], home)
        assert result.exit_code == 0
        assert "secret-token" not in result.output
        assert "https://hooks.slack.com" in result.output


def test_config_show_masks_smtp_password():
    with tempfile.TemporaryDirectory() as home:
        result = invoke([
            "alert", "email", "set",
            "--smtp-host", "smtp.test.com",
            "--smtp-pass", "secret",
            "--to-addrs", "alerts@test.com",
        ], home)
        assert result.exit_code == 0

        result = invoke(["config"], home)
        assert result.exit_code == 0
        assert "c2VjcmV0" not in result.output  # base64 of "secret"
        assert "secret" not in result.output
        assert "***" in result.output


def test_import_skips_invalid_entries():
    with tempfile.TemporaryDirectory() as home:
        backup = Path(home) / "backup.json"
        backup.write_text(json.dumps({
            "watches": [
                {"name": "good", "url": "https://x.test", "interval": 60},
                {"name": "bad-url", "url": "ftp://x.test", "interval": 60},
                {"name": "bad-interval", "url": "https://y.test", "interval": -5},
                {"name": "bad-interval-type", "url": "https://y.test", "interval": "abc"},
                {"name": "bad: name", "url": "https://z.test", "interval": 60},
                {"name": "bad-tags", "url": "https://t.test", "interval": 60, "tags": "oops"},
                {"name": "bad-headers", "url": "https://h.test", "interval": 60, "headers": ["oops"]},
            ],
            "snapshots": {},
        }), encoding="utf-8")

        result = invoke(["import", str(backup)], home)
        assert result.exit_code == 0
        assert "Imported 1 watch(es)" in result.output
        assert "skipped 6 invalid" in result.output

        storage = Storage(Path(home))
        assert storage.get_watch("good") is not None
        assert storage.get_watch("bad-url") is None
        assert storage.get_watch("bad-interval") is None
        assert storage.get_watch("bad-tags") is None


def test_import_csv_selector_column_and_bad_rows():
    with tempfile.TemporaryDirectory() as home:
        csv_file = Path(home) / "watches.csv"
        csv_file.write_text(
            "name,url,selector,interval,tags\n"
            "t1,https://x.test,#main,60,news;tech\n"
            "t2,https://y.test,,abc,\n"
            "t3,https://z.test,,-5,\n"
            "t4,https://w.test,,,\n",
            encoding="utf-8",
        )

        result = invoke(["import-csv", str(csv_file)], home)
        assert result.exit_code == 0
        assert "Imported 2 watch(es)" in result.output
        assert "Skipped 2" in result.output

        storage = Storage(Path(home))
        assert storage.get_watch("t1")["selector"] == "#main"
        assert storage.get_watch("t1")["tags"] == ["news", "tech"]
        assert storage.get_watch("t2") is None
        assert storage.get_watch("t3") is None
        assert storage.get_watch("t4")["interval"] == 3600  # empty cell falls back to default


def test_config_set_store_html_and_max_history():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))

        for val in ("true", "yes", "on", "1"):
            assert invoke(["config", "set", "store_html", val], home).exit_code == 0
            assert storage.load_config()["store_html"] is True
        for val in ("false", "no", "off", "0"):
            assert invoke(["config", "set", "store_html", val], home).exit_code == 0
            assert storage.load_config()["store_html"] is False
        assert invoke(["config", "set", "store_html", "maybe"], home).exit_code == 1

        assert invoke(["config", "set", "max_history", "50"], home).exit_code == 0
        assert storage.load_config()["max_history"] == 50
        for bad in ("0", "abc"):
            assert invoke(["config", "set", "max_history", bad], home).exit_code == 1
        # "-1" needs '--' so click does not treat it as an option.
        assert invoke(["config", "set", "max_history", "--", "-1"], home).exit_code == 1


def test_export_include_html_warns_when_html_storage_disabled():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)

        result = invoke(["export", "--include-html"], home)
        assert result.exit_code == 0
        latest = json.loads(result.output)["snapshots"]["t1"]["latest"]
        assert latest["html"] == PAGE_V1

        # Disable HTML storage; the next check stores an empty html field.
        assert invoke(["config", "set", "store_html", "false"], home).exit_code == 0
        configured = Storage(Path(home))
        with patched(monitor=Monitor(storage=configured, fetcher=StaticFetcher(PAGE_V2))):
            invoke(["check", "--no-alerts"], home)
        assert configured.load_snapshot("t1")["latest"]["html"] == ""

        result = invoke(["export", "--include-html"], home)
        assert result.exit_code == 0
        assert "HTML storage is disabled" in result.stderr
        latest = json.loads(result.stdout)["snapshots"]["t1"]["latest"]
        assert latest["html"] == ""
        # Text/diff data is unaffected by disabled HTML storage.
        assert latest["full_text"]


def test_stats_command_shows_disk_usage():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        invoke(["add", "https://x.test", "--name", "t1"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1))):
            invoke(["check", "--no-alerts"], home)
        result = invoke(["stats"], home)
        assert result.exit_code == 0
        assert "PageWatch Statistics" in result.output
        assert "Disk usage" in result.output
        assert "t1" in result.output


def test_install_service_unsupported_platform(monkeypatch):
    with tempfile.TemporaryDirectory() as home:
        monkeypatch.setattr(sys, "platform", "plan9")
        result = invoke(["install-service"], home)
        assert result.exit_code == 1
        assert "not supported" in result.output


def test_install_service_windows_shows_task_scheduler_hint(monkeypatch):
    with tempfile.TemporaryDirectory() as home:
        monkeypatch.setattr(sys, "platform", "win32")
        # Python 3.12's shutil.which() calls _winapi.NeedCurrentDirectoryForExePath()
        # on its Windows branch, and _winapi is None on Linux, so the fake
        # platform would crash executable resolution. Stub it out — the hint
        # text does not depend on the resolved path.
        monkeypatch.setattr(shutil, "which", lambda cmd, **kwargs: None)
        result = invoke(["install-service"], home)
        assert result.exit_code == 1
        assert "schtasks" in result.output


def test_uninstall_service_unsupported_platform(monkeypatch):
    with tempfile.TemporaryDirectory() as home:
        monkeypatch.setattr(sys, "platform", "plan9")
        result = invoke(["uninstall-service"], home)
        assert result.exit_code == 1
        assert "not supported" in result.output


def test_feed_command_outputs_valid_rss():
    with tempfile.TemporaryDirectory() as home:
        assert invoke(["add", "example.com", "--name", "t1"], home).exit_code == 0
        store = Storage(Path(home))
        store.save_snapshot("t1", "h1", "v1", "")
        store.save_snapshot("t1", "h2", "v2", "", diff="-v1\n+v2")

        result = invoke(["feed"], home)
        assert result.exit_code == 0
        minidom.parseString(result.output)
        assert "Change detected on t1" in result.output

        result = invoke(["feed", "t1"], home)
        assert result.exit_code == 0
        minidom.parseString(result.output)

        result = invoke(["feed", "nope"], home)
        assert result.exit_code == 1
        assert "not found" in result.output


def test_feed_command_empty_history_still_valid():
    with tempfile.TemporaryDirectory() as home:
        assert invoke(["add", "example.com", "--name", "t1"], home).exit_code == 0
        result = invoke(["feed"], home)
        assert result.exit_code == 0
        doc = minidom.parseString(result.output)
        assert doc.getElementsByTagName("item") == []


def test_import_from_changedetection():
    with tempfile.TemporaryDirectory() as home:
        export = Path(home) / "cd.json"
        export.write_text(json.dumps({
            "uuid-1": {
                "url": "https://example.com/pricing",
                "title": "Pricing page",
                "tag": "saas",
                "css_filter": "css:.price-block",
                "time_between_check": {"hours": 2},
            },
            "uuid-2": {
                "url": "https://news.example.com",
                "title": "News",
                "css_filter": "xpath://div[@id='ads']",
            },
        }), encoding="utf-8")

        result = invoke(["import", str(export), "--from", "changedetection"], home)
        assert result.exit_code == 0
        assert "Imported 2 watch(es)" in result.output
        assert "xpath" in result.output  # warning about the dropped xpath filter

        storage = Storage(Path(home))
        w = storage.get_watch("Pricing page")
        assert w is not None
        assert w["url"] == "https://example.com/pricing"
        assert w["selector"] == ".price-block"
        assert w["interval"] == 7200
        assert w["tags"] == ["saas"]
        w2 = storage.get_watch("News")
        assert w2 is not None
        assert not w2.get("selector")

        # Merging again skips the existing names.
        result = invoke(["import", str(export), "--from", "changedetection"], home)
        assert result.exit_code == 0
        assert "Imported 0 watch(es)" in result.output
        assert "skipped 2 existing" in result.output


def test_import_from_distill():
    with tempfile.TemporaryDirectory() as home:
        export = Path(home) / "distill.json"
        export.write_text(json.dumps({
            "client": {"local": 1},
            "data": [{
                "name": "GPU Tracker",
                "uri": "https://store.example.com/gpus",
                "config": json.dumps({
                    "selections": [{"frames": [{"includes": [{"expr": ".price", "type": "css"}]}]}],
                }),
                "schedule": json.dumps({"type": "INTERVAL", "params": {"interval": 284}}),
                "tags": ["gpus"],
            }],
        }), encoding="utf-8")

        result = invoke(["import", str(export), "--from", "distill"], home)
        assert result.exit_code == 0
        assert "Imported 1 watch(es)" in result.output

        storage = Storage(Path(home))
        w = storage.get_watch("GPU Tracker")
        assert w is not None
        assert w["url"] == "https://store.example.com/gpus"
        assert w["selector"] == ".price"
        assert w["interval"] == 284
        assert w["tags"] == ["gpus"]


def test_import_from_bad_option_or_file():
    with tempfile.TemporaryDirectory() as home:
        export = Path(home) / "cd.json"
        export.write_text("{not json", encoding="utf-8")

        # Unknown --from value is rejected by click.
        result = invoke(["import", str(export), "--from", "visualping"], home)
        assert result.exit_code != 0

        # Unparseable export fails cleanly.
        result = invoke(["import", str(export), "--from", "changedetection"], home)
        assert result.exit_code == 1
        assert "not a valid changedetection.io export" in result.output
