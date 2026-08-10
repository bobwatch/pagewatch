import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import requests

from pagewatch.alerts import AlertManager
from pagewatch.monitor import Monitor
from pagewatch.server import PagewatchServer
from pagewatch.storage import Storage

PAGE_V1 = "<html><body><p>version one</p></body></html>"
PAGE_V2 = "<html><body><p>version two</p></body></html>"


class SeqFetcher:
    def __init__(self, *pages):
        self.pages = list(pages) or [PAGE_V1]

    def __call__(self, url, **kwargs):
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(page, Exception):
            raise page
        return page, url


class FakeResponse:
    status_code = 200


class FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()


@contextmanager
def running_server(*pages, webui_dir=None, token=None):
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
            webui_dir=webui_dir or (Path(tmp) / "no-webui"),
            token=token,
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}", store, fake_session
        finally:
            server.shutdown()
            server.server_close()


def test_status_endpoint():
    with running_server() as (base, _store, _session):
        data = requests.get(f"{base}/api/status", timeout=5).json()
        assert data["watch_count"] == 0
        assert data["ui_built"] is False
        assert "slack" in data["alert_formats"]
        assert data["version"]


def test_watch_crud_and_validation():
    with running_server() as (base, store, _session):
        # invalid URL
        r = requests.post(f"{base}/api/watches", json={"url": "https://"}, timeout=5)
        assert r.status_code == 400

        # create
        r = requests.post(f"{base}/api/watches", json={
            "url": "x.test", "name": "t1", "interval": 60,
            "ignore_patterns": [r"\d+ views"],
        }, timeout=5)
        assert r.status_code == 201
        watch = r.json()["watch"]
        assert watch["url"] == "https://x.test"
        assert watch["ignore_patterns"] == [r"\d+ views"]

        # duplicate name
        r = requests.post(f"{base}/api/watches", json={"url": "y.test", "name": "t1"}, timeout=5)
        assert r.status_code == 409

        # invalid regex
        r = requests.post(f"{base}/api/watches", json={"url": "y.test", "ignore_patterns": ["[bad"]}, timeout=5)
        assert r.status_code == 400

        # list
        r = requests.get(f"{base}/api/watches", timeout=5)
        assert [w["name"] for w in r.json()] == ["t1"]

        # patch interval only: no baseline reset
        store.update_watch("t1", last_hash="somehash")
        r = requests.patch(f"{base}/api/watches/t1", json={"interval": 120}, timeout=5)
        assert r.status_code == 200
        assert r.json()["baseline_reset"] is False
        assert store.get_watch("t1")["last_hash"] == "somehash"

        # patch ignore patterns: baseline reset
        r = requests.patch(f"{base}/api/watches/t1", json={"ignore_patterns": []}, timeout=5)
        assert r.json()["baseline_reset"] is True
        assert store.get_watch("t1")["last_hash"] is None

        # patch validation
        assert requests.patch(f"{base}/api/watches/t1", json={}, timeout=5).status_code == 400
        assert requests.patch(f"{base}/api/watches/nope", json={"interval": 5}, timeout=5).status_code == 404

        # delete
        assert requests.delete(f"{base}/api/watches/t1", timeout=5).status_code == 200
        assert requests.delete(f"{base}/api/watches/t1", timeout=5).status_code == 404


def test_add_with_check_now():
    with running_server(PAGE_V1) as (base, _store, _session):
        r = requests.post(f"{base}/api/watches", json={
            "url": "x.test", "name": "t1", "check_now": True,
        }, timeout=5)
        assert r.status_code == 201
        body = r.json()
        assert body["result"]["error"] is None
        assert body["watch"]["last_hash"] == body["result"]["current_hash"]


def test_check_flow_with_alerts():
    with running_server(PAGE_V1, PAGE_V2) as (base, _store, session):
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1"}, timeout=5)
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/x", "name": "ops", "format": "slack", "events": "change",
        }, timeout=5)

        # baseline: no change, no alert
        data = requests.post(f"{base}/api/check", json={}, timeout=5).json()
        assert data["results"][0]["changed"] is False
        assert session.posts == []

        # change detected via per-watch check: alert dispatched
        data = requests.post(f"{base}/api/watches/t1/check", json={}, timeout=5).json()
        assert data["result"]["changed"] is True
        assert "+version two" in data["result"]["diff"]
        assert len(session.posts) == 1
        assert len(data["alerts"]) == 2  # 1 webhook + 1 email report (email not configured)

        # alerts can be suppressed
        requests.patch(f"{base}/api/watches/t1", json={"ignore_patterns": []}, timeout=5)  # reset baseline
        data = requests.post(f"{base}/api/watches/t1/check", json={"alerts": False}, timeout=5).json()
        assert len(session.posts) == 1


def test_history_and_diff_endpoints():
    with running_server(PAGE_V1, PAGE_V2) as (base, _store, _session):
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1"}, timeout=5)

        data = requests.get(f"{base}/api/watches/t1/diff", timeout=5).json()
        assert data["diff"] is None

        requests.post(f"{base}/api/watches/t1/check", json={"alerts": False}, timeout=5)
        requests.post(f"{base}/api/watches/t1/check", json={"alerts": False}, timeout=5)

        data = requests.get(f"{base}/api/watches/t1/history?limit=1", timeout=5).json()
        assert data["total"] == 2
        assert len(data["history"]) == 1
        assert data["latest"]["content_hash"]
        assert data["previous"]["content_hash"]

        data = requests.get(f"{base}/api/watches/t1/diff", timeout=5).json()
        assert "+version two" in data["diff"]
        assert requests.get(f"{base}/api/watches/nope/diff", timeout=5).status_code == 404


def test_config_endpoints():
    with running_server() as (base, store, _session):
        data = requests.get(f"{base}/api/config", timeout=5).json()
        assert data["retries"] == 2

        r = requests.put(f"{base}/api/config", json={"interval": 900, "proxy": "http://p:1", "retries": 4},
                         timeout=5)
        assert r.status_code == 200
        cfg = store.load_config()
        assert cfg["interval"] == 900
        assert cfg["proxy"] == "http://p:1"
        assert cfg["retries"] == 4

        assert requests.put(f"{base}/api/config", json={"proxy": "none"}, timeout=5).status_code == 200
        assert store.load_config()["proxy"] is None

        assert requests.put(f"{base}/api/config", json={"retries": 99}, timeout=5).status_code == 400
        assert requests.put(f"{base}/api/config", json={"interval": 0}, timeout=5).status_code == 400
        assert requests.put(f"{base}/api/config", json={"nope": 1}, timeout=5).status_code == 400


def test_alert_endpoints():
    with running_server() as (base, _store, _session):
        r = requests.post(f"{base}/api/alerts", json={"url": "https://hooks.example/a", "name": "ops",
                                                       "format": "feishu", "events": "all"}, timeout=5)
        assert r.status_code == 201

        assert requests.post(f"{base}/api/alerts", json={"url": "bad"}, timeout=5).status_code == 400

        channels = requests.get(f"{base}/api/alerts", timeout=5).json()
        assert channels[0]["name"] == "ops"

        data = requests.post(f"{base}/api/alerts/test", json={"name": "ops"}, timeout=5).json()
        assert data["deliveries"][0]["ok"] is True
        assert requests.post(f"{base}/api/alerts/test", json={"name": "nope"}, timeout=5).status_code == 404

        assert requests.delete(f"{base}/api/alerts/ops", timeout=5).status_code == 200
        assert requests.delete(f"{base}/api/alerts/ops", timeout=5).status_code == 404


def test_pause_resume_endpoints():
    with running_server(PAGE_V1) as (base, store, _session):
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1"}, timeout=5)

        r = requests.post(f"{base}/api/watches/t1/pause", timeout=5)
        assert r.status_code == 200
        assert r.json()["paused"] is True
        assert store.get_watch("t1")["paused"] is True

        r = requests.post(f"{base}/api/watches/t1/resume", timeout=5)
        assert r.status_code == 200
        assert r.json()["paused"] is False
        assert store.get_watch("t1")["paused"] is False

        assert requests.post(f"{base}/api/watches/nope/pause", timeout=5).status_code == 404


def test_email_config_endpoints():
    with running_server() as (base, _store, _session):
        r = requests.get(f"{base}/api/alerts/email", timeout=5)
        assert r.status_code == 200
        assert r.json().get("smtp_host", "") == ""

        r = requests.put(f"{base}/api/alerts/email", json={
            "smtp_host": "smtp.test.com",
            "smtp_port": 587,
            "smtp_user": "user@test.com",
            "smtp_pass": "secret",
            "to_addrs": "alerts@test.com",
        }, timeout=5)
        assert r.status_code == 200
        assert r.json()["smtp_host"] == "smtp.test.com"
        assert r.json()["smtp_pass_set"] is True

        r = requests.get(f"{base}/api/alerts/email", timeout=5)
        assert r.json()["smtp_host"] == "smtp.test.com"
        assert "smtp_pass" not in r.json()  # password is never returned
        assert r.json()["smtp_pass_set"] is True


def test_status_includes_paused_count():
    with running_server() as (base, store, _session):
        store.add_watch("t1", "https://x.test")
        store.add_watch("t2", "https://y.test", paused=True)
        data = requests.get(f"{base}/api/status", timeout=5).json()
        assert data["watch_count"] == 2
        assert data["paused_count"] == 1
        assert data["email_configured"] is False


def test_update_alert_endpoint():
    with running_server() as (base, _store, _session):
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/a", "name": "ops", "format": "slack", "events": "change",
        }, timeout=5)
        r = requests.patch(f"{base}/api/alerts/ops", json={"format": "discord", "events": "all"}, timeout=5)
        assert r.status_code == 200
        assert r.json()["format"] == "discord"

        r = requests.patch(f"{base}/api/alerts/nope", json={"format": "discord"}, timeout=5)
        assert r.status_code == 404


def test_export_endpoint():
    with running_server(PAGE_V1) as (base, store, _session):
        store.add_watch("t1", "https://x.test")
        r = requests.get(f"{base}/api/export", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert len(data["watches"]) == 1
        assert data["watches"][0]["name"] == "t1"


def test_daemon_endpoints():
    with running_server() as (base, _store, _session):
        r = requests.get(f"{base}/api/daemon/status", timeout=5)
        assert r.status_code == 200
        assert r.json()["running"] is False

        r = requests.post(f"{base}/api/daemon/start", timeout=5)
        assert r.status_code == 200
        assert r.json()["running"] is True

        r = requests.get(f"{base}/api/daemon/status", timeout=5)
        assert r.json()["running"] is True

        r = requests.post(f"{base}/api/daemon/stop", timeout=5)
        assert r.status_code == 200
        assert r.json()["running"] is False


def test_delete_history_endpoint():
    with running_server(PAGE_V1, PAGE_V2) as (base, store, _session):
        store.add_watch("t1", "https://x.test")
        with store._snapshots_dir.joinpath("t1.json").open("w") as f:
            import json
            json.dump({"history": [{"ts": "t1"}]}, f)
        assert store._snapshots_dir.joinpath("t1.json").is_file()
        r = requests.delete(f"{base}/api/watches/t1/history", timeout=5)
        assert r.status_code == 200
        assert not store._snapshots_dir.joinpath("t1.json").is_file()

        r = requests.delete(f"{base}/api/watches/nope/history", timeout=5)
        assert r.status_code == 404


def test_unknown_api_route_is_404_json():
    with running_server() as (base, _store, _session):
        r = requests.get(f"{base}/api/nope", timeout=5)
        assert r.status_code == 404
        assert "error" in r.json()


def test_static_fallback_page_without_ui():
    with running_server() as (base, _store, _session):
        r = requests.get(base + "/", timeout=5)
        assert r.status_code == 200
        assert "PageWatch API is running" in r.text


def test_static_serving_and_spa_fallback():
    with tempfile.TemporaryDirectory() as ui:
        ui_dir = Path(ui)
        (ui_dir / "assets").mkdir()
        (ui_dir / "index.html").write_text("<html><body>SPA-INDEX</body></html>", encoding="utf-8")
        (ui_dir / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")

        with running_server(webui_dir=ui_dir) as (base, _store, _session):
            assert "SPA-INDEX" in requests.get(base + "/", timeout=5).text

            r = requests.get(base + "/assets/app.js", timeout=5)
            assert r.status_code == 200
            assert "console.log" in r.text
            assert "javascript" in r.headers["Content-Type"]

            # unknown paths fall back to the SPA index
            assert "SPA-INDEX" in requests.get(base + "/watches/deep/route", timeout=5).text

            # path traversal is refused
            r = requests.get(base + "/%2e%2e/%2e%2e/etc/passwd", timeout=5)
            assert r.status_code == 403


# -- security hardening --------------------------------------------------------


def test_no_cors_headers():
    with running_server() as (base, _store, _session):
        r = requests.get(f"{base}/api/status", timeout=5)
        assert r.status_code == 200
        assert "Access-Control-Allow-Origin" not in r.headers

        r = requests.options(f"{base}/api/status", timeout=5)
        assert r.status_code == 405
        assert "Access-Control-Allow-Origin" not in r.headers


def test_host_header_validation_on_loopback():
    with running_server() as (base, _store, _session):
        # hostile Host header (DNS rebinding) is refused
        r = requests.get(f"{base}/api/status", headers={"Host": "evil.example.com"}, timeout=5)
        assert r.status_code == 403

        # loopback hostnames with any port are accepted
        for host in ("localhost:8787", "127.0.0.1:9999", "[::1]:8080", "localhost"):
            r = requests.get(f"{base}/api/status", headers={"Host": host}, timeout=5)
            assert r.status_code == 200, host


def test_token_auth():
    with running_server(token="s3cret") as (base, _store, _session):
        r = requests.get(f"{base}/api/status", timeout=5)
        assert r.status_code == 401
        assert "error" in r.json()

        r = requests.get(f"{base}/api/status", headers={"Authorization": "Bearer wrong"}, timeout=5)
        assert r.status_code == 401

        r = requests.get(f"{base}/api/status", headers={"Authorization": "Bearer s3cret"}, timeout=5)
        assert r.status_code == 200

        # static UI is not gated
        assert requests.get(base + "/", timeout=5).status_code == 200

        # with a token configured, the Host check is skipped (user's responsibility)
        r = requests.get(f"{base}/api/status",
                         headers={"Authorization": "Bearer s3cret", "Host": "evil.example.com"}, timeout=5)
        assert r.status_code == 200


def test_token_from_environment(monkeypatch):
    monkeypatch.setenv("PAGEWATCH_TOKEN", "envtoken")
    with running_server() as (base, _store, _session):
        assert requests.get(f"{base}/api/status", timeout=5).status_code == 401
        r = requests.get(f"{base}/api/status", headers={"Authorization": "Bearer envtoken"}, timeout=5)
        assert r.status_code == 200


def test_config_endpoint_redacts_credentials():
    with running_server() as (base, store, _session):
        requests.put(f"{base}/api/alerts/email", json={
            "smtp_host": "smtp.test.com", "smtp_port": 587, "smtp_pass": "topsecret",
        }, timeout=5)
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/secret-token-path", "name": "ops",
        }, timeout=5)

        r = requests.get(f"{base}/api/config", timeout=5)
        assert r.status_code == 200
        assert "topsecret" not in r.text
        assert "secret-token-path" not in r.text
        cfg = r.json()
        assert cfg["alerts"]["email"]["smtp_pass_obfuscated"] == "***"
        assert cfg["alerts"]["webhooks"][0]["url"] == "https://hooks.example/…"

        # stored config is untouched
        stored = store.load_config()
        assert stored["alerts"]["webhooks"][0]["url"] == "https://hooks.example/secret-token-path"


def test_alert_list_masks_url_and_update_keeps_masked_url():
    with running_server() as (base, store, _session):
        requests.post(f"{base}/api/alerts", json={
            "url": "https://hooks.example/real-path", "name": "ops",
        }, timeout=5)

        channels = requests.get(f"{base}/api/alerts", timeout=5).json()
        masked = channels[0]["url"]
        assert masked == "https://hooks.example/…"

        # saving back the displayed mask must not overwrite the real URL
        r = requests.patch(f"{base}/api/alerts/ops", json={"url": masked, "format": "discord"}, timeout=5)
        assert r.status_code == 200
        webhooks = store.load_config()["alerts"]["webhooks"]
        assert webhooks[0]["url"] == "https://hooks.example/real-path"
        assert webhooks[0]["format"] == "discord"

        # a literal *** placeholder is also ignored
        r = requests.patch(f"{base}/api/alerts/ops", json={"url": "***"}, timeout=5)
        assert r.status_code == 200
        assert store.load_config()["alerts"]["webhooks"][0]["url"] == "https://hooks.example/real-path"

        # a genuinely new URL still updates
        r = requests.patch(f"{base}/api/alerts/ops", json={"url": "https://hooks.example/new"}, timeout=5)
        assert r.status_code == 200
        assert store.load_config()["alerts"]["webhooks"][0]["url"] == "https://hooks.example/new"


def test_import_validation_all_or_nothing():
    with running_server() as (base, store, _session):
        good = {"name": "ok", "url": "https://x.test", "interval": 60, "tags": ["a"],
                "headers": {"X-Foo": "bar"}}

        # path-traversal name
        r = requests.post(f"{base}/api/import", json={
            "watches": [good, {"name": "../evil", "url": "https://y.test"}]}, timeout=5)
        assert r.status_code == 400
        assert r.json()["details"]
        assert store.load_watches() == []

        # non-http(s) URL
        r = requests.post(f"{base}/api/import", json={
            "watches": [{"name": "ftp", "url": "ftp://files.example/x"}]}, timeout=5)
        assert r.status_code == 400
        assert store.load_watches() == []

        # bad interval / tags / headers / selector
        bad_variants = [
            {"name": "w", "url": "https://x.test", "interval": 0},
            {"name": "w", "url": "https://x.test", "interval": "soon"},
            {"name": "w", "url": "https://x.test", "tags": ["a", 1]},
            {"name": "w", "url": "https://x.test", "headers": {"X": 5}},
            {"name": "w", "url": "https://x.test", "selector": "div >>"},
        ]
        for i, bad in enumerate(bad_variants):
            r = requests.post(f"{base}/api/import", json={"watches": [bad]}, timeout=5)
            assert r.status_code == 400, i
            assert store.load_watches() == []

        # a fully valid payload imports
        r = requests.post(f"{base}/api/import", json={"watches": [good]}, timeout=5)
        assert r.status_code == 200
        assert r.json()["imported"] == 1
        assert [w["name"] for w in store.load_watches()] == ["ok"]


def test_500_does_not_leak_details(monkeypatch):
    with running_server() as (base, store, _session):
        def boom():
            raise RuntimeError("sensitive C:\\Users\\bob path with credentials")

        monkeypatch.setattr(store, "load_watches", boom)
        r = requests.get(f"{base}/api/status", timeout=5)
        assert r.status_code == 500
        assert r.json()["error"] == "Internal server error"
        assert "sensitive" not in r.text
        assert "bob" not in r.text


def test_email_config_invalid_port_is_400():
    with running_server() as (base, _store, _session):
        r = requests.put(f"{base}/api/alerts/email", json={
            "smtp_host": "smtp.test.com", "smtp_port": "abc",
        }, timeout=5)
        assert r.status_code == 400


def test_clone_watch_response_shape():
    with running_server() as (base, _store, _session):
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1"}, timeout=5)
        r = requests.post(f"{base}/api/watches/t1/clone", json={"name": "t2"}, timeout=5)
        assert r.status_code == 201
        body = r.json()
        assert body["watch"]["name"] == "t2"


def test_delete_history_resets_last_hash():
    with running_server(PAGE_V1) as (base, store, _session):
        requests.post(f"{base}/api/watches", json={"url": "x.test", "name": "t1", "check_now": True}, timeout=5)
        assert store.get_watch("t1")["last_hash"]
        assert store._snapshots_dir.joinpath("t1.json").is_file()

        r = requests.delete(f"{base}/api/watches/t1/history", timeout=5)
        assert r.status_code == 200
        assert store.get_watch("t1")["last_hash"] is None
        assert not store._snapshots_dir.joinpath("t1.json").is_file()

        # existing watch but no snapshot file: 404, same as delete-watch semantics
        r = requests.delete(f"{base}/api/watches/t1/history", timeout=5)
        assert r.status_code == 404


def test_daemon_tick_tolerates_bad_watch_data():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp) / "data")
        fetcher = SeqFetcher(PAGE_V1)

        def monitor_factory():
            return Monitor(storage=store, fetcher=fetcher)

        server = PagewatchServer(("127.0.0.1", 0), storage=store, monitor_factory=monitor_factory,
                                 webui_dir=Path(tmp) / "no-webui")
        try:
            store.add_watch("t1", "https://x.test")
            # garbage that could arrive via a hand-edited/imported watches.json
            store.update_watch("t1", interval="not-a-number", last_checked=12345)
            server._daemon_tick()  # must not raise
            assert store.get_watch("t1")["last_hash"]
        finally:
            server.server_close()


def test_daemon_loop_survives_tick_errors():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp) / "data")
        server = PagewatchServer(("127.0.0.1", 0), storage=store, webui_dir=Path(tmp) / "no-webui")

        def boom():
            raise RuntimeError("disk exploded")

        server._daemon_tick = boom
        server.start_daemon()
        try:
            time.sleep(0.5)
            assert server.daemon_running  # exception was logged, thread kept going
        finally:
            server.stop_daemon()
            server.server_close()
