import json
import tempfile
import threading
from contextlib import contextmanager
from email import message_from_string
from pathlib import Path

import requests
from click.testing import CliRunner

import pagewatch.cli as cli_module
from pagewatch.alerts import AlertManager, build_payload
from pagewatch.cli import cli
from pagewatch.monitor import Monitor
from pagewatch.server import PagewatchServer
from pagewatch.storage import Storage

PAGE_V1 = "<html><body><p>version one</p></body></html>"
PAGE_V2 = "<html><body><p>version two</p></body></html>"
PAGE_V3 = "<html><body><p>version three</p></body></html>"


class SeqFetcher:
    def __init__(self, *pages):
        self.pages = list(pages) or [PAGE_V1]

    def __call__(self, url, **kwargs):
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(page, Exception):
            raise page
        return page, url


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


@contextmanager
def manager():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        session = FakeSession()
        yield AlertManager(storage=store, session=session), session, store


def changed_result(name="w", diff="-old\n+new\n"):
    return {
        "name": name,
        "url": f"https://{name}.test",
        "timestamp": "2026-08-10T00:00:00+00:00",
        "changed": True,
        "error": None,
        "diff": diff,
        "current_hash": "abc123",
        "consecutive_errors": 0,
    }


def error_result(name="w", consecutive=1):
    return {
        "name": name,
        "url": f"https://{name}.test",
        "timestamp": "2026-08-10T00:00:00+00:00",
        "changed": False,
        "error": "HTTP 500",
        "diff": None,
        "consecutive_errors": consecutive,
    }


def recovered_result(name="w", after=3):
    return {
        "name": name,
        "url": f"https://{name}.test",
        "timestamp": "2026-08-10T00:00:00+00:00",
        "changed": False,
        "error": None,
        "diff": None,
        "consecutive_errors": 0,
        "error_recovered": True,
        "recovered_after": after,
    }


# -- alert_filter (dispatch level) -------------------------------------------


def test_alert_filter_suppresses_non_matching_diff():
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/a", name="ops", events="change")
        watches = {"w": {"name": "w", "alert_filter": "price"}}
        result = changed_result(diff="-old\n+new\n")
        deliveries = am.dispatch([result], watches=watches)
        assert session.posts == []
        assert deliveries == []
        # detection/recording is unaffected — only the alert is filtered
        assert result["changed"] is True
        assert result["alert_suppressed"] is True


def test_alert_filter_dispatches_matching_diff():
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/a", name="ops", events="change")
        watches = {"w": {"name": "w", "alert_filter": "price|new"}}
        result = changed_result(diff="-old\n+new\n")
        deliveries = am.dispatch([result], watches=watches)
        assert len(session.posts) == 1
        assert "alert_suppressed" not in result
        assert any(d["ok"] for d in deliveries)


def test_alert_filter_invalid_regex_still_alerts():
    # Entry points validate the regex; if an invalid one slips through it must
    # not silently swallow alerts.
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/a", name="ops", events="change")
        watches = {"w": {"name": "w", "alert_filter": "[bad"}}
        am.dispatch([changed_result()], watches=watches)
        assert len(session.posts) == 1


def test_dispatch_without_watches_keeps_legacy_behavior():
    with manager() as (am, session, store):
        am.add_channel("https://hooks.example/a", name="ops", events="error")
        store.save_config({**store.load_config(), "error_threshold": 99})
        # no watches mapping: threshold/filter/recovery logic stays off
        deliveries = am.dispatch([error_result(consecutive=1)])
        assert len(session.posts) == 1
        assert any(d.get("ok") for d in deliveries)
        am.dispatch([recovered_result()])
        assert len(session.posts) == 1  # recovery only fires with watches


# -- error threshold + recovery (dispatch level) ------------------------------


def test_error_threshold_alerts_once_at_threshold():
    with manager() as (am, session, store):
        store.add_watch(name="w", url="https://w.test")
        am.add_channel("https://hooks.example/err", name="on-error", events="error")
        store.save_config({**store.load_config(), "error_threshold": 3})
        watches = {"w": store.get_watch("w")}

        assert am.dispatch([error_result(consecutive=1)], watches=watches) == []
        assert am.dispatch([error_result(consecutive=2)], watches=watches) == []
        assert session.posts == []

        deliveries = am.dispatch([error_result(consecutive=3)], watches=watches)
        assert len(session.posts) == 1
        assert session.posts[0]["json"]["event"] == "error"
        assert any(d["ok"] for d in deliveries)
        # flagged so a later success emits exactly one recovery event
        assert store.get_watch("w")["error_alerted"] is True

        # beyond the threshold it stays quiet
        assert am.dispatch([error_result(consecutive=4)], watches=watches) == []
        assert len(session.posts) == 1


def test_recovery_goes_only_to_all_channels():
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/change", name="on-change", events="change")
        am.add_channel("https://hooks.example/error", name="on-error", events="error")
        am.add_channel("https://hooks.example/all", name="on-all", events="all")
        deliveries = am.dispatch([recovered_result(after=3)], watches={"w": {}})
        assert [p["url"] for p in session.posts] == ["https://hooks.example/all"]
        payload = session.posts[0]["json"]
        assert payload["event"] == "recovery"
        assert payload["recovered_after"] == 3
        webhook_reports = [d for d in deliveries if d.get("channel") != "email"]
        assert len(webhook_reports) == 1


def test_recovery_payload_text():
    payload = build_payload("slack", {"event": "recovery", "name": "w",
                                      "url": "https://w.test", "recovered_after": 3})
    assert payload["text"] == "PageWatch: 'w' recovered after 3 consecutive errors"


def test_recovery_email_sent(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, from_addr, to_addrs, msg):
            sent.append(msg)

    monkeypatch.setattr("pagewatch.alerts.smtplib.SMTP", FakeSMTP)
    with manager() as (am, _session, _store):
        am.set_email_config(smtp_host="smtp.test", to_addrs="ops@example.com")
        am.dispatch([recovered_result(after=3)], watches={"w": {}})
        assert len(sent) == 1
        msg = message_from_string(sent[0])
        assert msg["Subject"] == "[PageWatch] Recovered: w"
        body = msg.get_payload(decode=True).decode("utf-8")
        assert "recovered after 3 consecutive errors" in body


# -- monitor counters ----------------------------------------------------------


def test_check_one_tracks_consecutive_errors():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        store.add_watch(name="w", url="https://w.test")
        monitor = Monitor(storage=store, fetcher=SeqFetcher(ValueError("boom")))

        r1 = monitor.check_one(store.get_watch("w"))
        assert r1["error"] and r1["consecutive_errors"] == 1
        assert store.get_watch("w")["consecutive_errors"] == 1

        r2 = monitor.check_one(store.get_watch("w"))
        assert r2["consecutive_errors"] == 2
        # error_count stays the cumulative counter
        assert store.get_watch("w")["error_count"] == 2


def test_check_one_flags_recovery_only_after_error_alert():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        store.add_watch(name="w", url="https://w.test")
        monitor = Monitor(storage=store, fetcher=SeqFetcher(ValueError("boom"), PAGE_V1))

        monitor.check_one(store.get_watch("w"))
        assert store.get_watch("w")["consecutive_errors"] == 1
        store.update_watch("w", error_alerted=True)  # dispatch side effect

        recovered = monitor.check_one(store.get_watch("w"))
        assert recovered["error"] is None
        assert recovered["error_recovered"] is True
        assert recovered["recovered_after"] == 1
        assert recovered["consecutive_errors"] == 0
        reloaded = store.get_watch("w")
        assert reloaded["error_alerted"] is False
        assert reloaded["consecutive_errors"] == 0

        # a plain success never flags a recovery
        plain = monitor.check_one(store.get_watch("w"))
        assert "error_recovered" not in plain


# -- CLI ------------------------------------------------------------------------


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


def test_add_alert_filter_stored_and_validated():
    with tempfile.TemporaryDirectory() as home:
        result = invoke(["add", "example.com", "--name", "t1", "--alert-filter", "price|stock"], home)
        assert result.exit_code == 0
        assert Storage(Path(home)).get_watch("t1")["alert_filter"] == "price|stock"

        result = invoke(["add", "example.org", "--name", "t2", "--alert-filter", "[bad"], home)
        assert result.exit_code == 1
        assert "invalid regex" in result.output


def test_update_alert_filter_set_and_clear():
    with tempfile.TemporaryDirectory() as home:
        invoke(["add", "example.com", "--name", "t1"], home)
        storage = Storage(Path(home))
        assert storage.get_watch("t1")["alert_filter"] is None

        result = invoke(["update", "t1", "--alert-filter", "price"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t1")["alert_filter"] == "price"

        result = invoke(["update", "t1", "--alert-filter", "[bad"], home)
        assert result.exit_code == 1

        result = invoke(["update", "t1", "--alert-filter", "x", "--clear-alert-filter"], home)
        assert result.exit_code == 1

        result = invoke(["update", "t1", "--clear-alert-filter"], home)
        assert result.exit_code == 0
        assert storage.get_watch("t1")["alert_filter"] is None


def test_config_set_error_threshold():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        assert storage.load_config().get("error_threshold", 1) == 1

        result = invoke(["config", "set", "error_threshold", "3"], home)
        assert result.exit_code == 0
        assert storage.load_config()["error_threshold"] == 3

        for bad in ("0", "-1", "abc"):
            result = invoke(["config", "set", "error_threshold", bad], home)
            assert result.exit_code != 0
        assert storage.load_config()["error_threshold"] == 3


def test_check_marks_alert_filtered_change():
    with tempfile.TemporaryDirectory() as home:
        storage = Storage(Path(home))
        session = FakeSession()
        alert_manager = AlertManager(storage=storage, session=session)
        invoke(["add", "https://x.test", "--name", "t1", "--alert-filter", "price"], home)
        alert_manager.add_channel("https://hooks.example/x", name="ops", events="change")

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V1)),
                     alert_manager=alert_manager):
            assert invoke(["check"], home).exit_code == 0
        assert session.posts == []  # baseline

        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V2)),
                     alert_manager=alert_manager):
            result = invoke(["check"], home)
        assert result.exit_code == 0
        assert "YES" in result.output
        assert "alert filtered" in result.output
        assert session.posts == []  # change detected but alert filtered out

        # matching filter lets the alert through
        invoke(["update", "t1", "--alert-filter", "version three"], home)
        with patched(monitor=Monitor(storage=storage, fetcher=StaticFetcher(PAGE_V3)),
                     alert_manager=alert_manager):
            result = invoke(["check"], home)
        assert result.exit_code == 0
        assert "alert filtered" not in result.output
        assert len(session.posts) == 1


# -- server ---------------------------------------------------------------------


@contextmanager
def running_server(*pages):
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp) / "data")
        fetcher = SeqFetcher(*pages)
        fake_session = FakeSession()

        def monitor_factory():
            return Monitor(storage=store, fetcher=fetcher)

        def alert_factory():
            return AlertManager(storage=store, session=fake_session)

        server = PagewatchServer(
            ("127.0.0.1", 0),
            storage=store,
            monitor_factory=monitor_factory,
            alert_manager_factory=alert_factory,
            webui_dir=Path(tmp) / "no-webui",
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}", store, fake_session
        finally:
            server.shutdown()
            server.server_close()


def test_server_alert_filter_validation():
    with running_server() as (base, store, _session):
        r = requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1",
                                                       "alert_filter": "[bad"}, timeout=5)
        assert r.status_code == 400

        r = requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1",
                                                       "alert_filter": "price|stock"}, timeout=5)
        assert r.status_code == 201
        assert r.json()["watch"]["alert_filter"] == "price|stock"

        r = requests.patch(f"{base}/api/watches/t1", json={"alert_filter": "(["}, timeout=5)
        assert r.status_code == 400

        r = requests.patch(f"{base}/api/watches/t1", json={"alert_filter": None}, timeout=5)
        assert r.status_code == 200
        assert store.get_watch("t1")["alert_filter"] is None


def test_server_import_rejects_invalid_alert_filter():
    with running_server() as (base, _store, _session):
        r = requests.post(f"{base}/api/import", json={
            "watches": [{"name": "t1", "url": "https://x.test", "alert_filter": "[bad"}],
        }, timeout=5)
        assert r.status_code == 400
        assert "alert_filter" in json.dumps(r.json()["details"])


def test_server_config_error_threshold():
    with running_server() as (base, store, _session):
        assert requests.get(f"{base}/api/config", timeout=5).json().get("error_threshold", 1) == 1
        for bad in (0, -1, "3", True):
            r = requests.put(f"{base}/api/config", json={"error_threshold": bad}, timeout=5)
            assert r.status_code == 400
        r = requests.put(f"{base}/api/config", json={"error_threshold": 3}, timeout=5)
        assert r.status_code == 200
        assert store.load_config()["error_threshold"] == 3


def test_server_check_honors_alert_filter():
    with running_server(PAGE_V1, PAGE_V2, PAGE_V3) as (base, _store, session):
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1",
                                                   "alert_filter": "price"}, timeout=5)
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/x", "name": "ops", "events": "change",
        }, timeout=5)

        data = requests.post(f"{base}/api/check", json={}, timeout=5).json()  # baseline
        assert data["results"][0]["changed"] is False

        data = requests.post(f"{base}/api/check", json={}, timeout=5).json()
        assert data["results"][0]["changed"] is True
        assert data["results"][0]["alert_suppressed"] is True
        assert data["alerts"] == []
        assert session.posts == []

        requests.patch(f"{base}/api/watches/t1", json={"alert_filter": "version three"}, timeout=5)
        data = requests.post(f"{base}/api/check", json={}, timeout=5).json()
        assert data["results"][0]["changed"] is True
        assert "alert_suppressed" not in data["results"][0]
        assert len(session.posts) == 1


def test_server_error_threshold_and_recovery_flow():
    pages = [ValueError("boom"), ValueError("boom"), ValueError("boom"), ValueError("boom"), PAGE_V1]
    with running_server(*pages) as (base, _store, session):
        requests.put(f"{base}/api/config", json={"error_threshold": 3}, timeout=5)
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1"}, timeout=5)
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/err", "name": "on-error", "events": "error",
        }, timeout=5)
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/all", "name": "on-all", "events": "all",
        }, timeout=5)

        def check():
            return requests.post(f"{base}/api/check", json={}, timeout=5).json()

        check()  # 1st failure: below threshold
        check()  # 2nd failure: below threshold
        assert session.posts == []

        data = check()  # 3rd failure: exactly at threshold
        assert data["results"][0]["consecutive_errors"] == 3
        assert sorted(p["url"] for p in session.posts) == [
            "https://hooks.example/all", "https://hooks.example/err",
        ]

        check()  # 4th failure: past threshold, no repeat
        assert len(session.posts) == 2

        data = check()  # recovery: one notice, only to the events=all channel
        assert data["results"][0]["error_recovered"] is True
        assert len(session.posts) == 3
        assert session.posts[-1]["url"] == "https://hooks.example/all"
        assert session.posts[-1]["json"]["event"] == "recovery"
        assert session.posts[-1]["json"]["recovered_after"] == 4
