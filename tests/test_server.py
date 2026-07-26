import tempfile
import threading
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

    def __call__(self, url):
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
def running_server(*pages, webui_dir=None):
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
    with running_server(PAGE_V1) as (base, store, _session):
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
        assert len(data["alerts"]) == 1

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
