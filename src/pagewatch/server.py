#!/usr/bin/env python
"""Local web dashboard for pagewatch.

A dependency-free HTTP server (stdlib only) that exposes the JSON-file
storage as a REST API and serves the React dashboard built from ``apps/web``.
All data stays in plain files under the pagewatch data directory — no
database, same storage the CLI uses.

Endpoints (all JSON):
    GET    /api/status
    GET    /api/watches
    POST   /api/watches                {url, name?, selector?, interval?, ignore_patterns?, check_now?}
    PATCH  /api/watches/{name}         {url?, selector?, interval?, ignore_patterns?}
    DELETE /api/watches/{name}
    POST   /api/watches/{name}/check   {alerts?: true}
    GET    /api/watches/{name}/history?limit=N
    GET    /api/watches/{name}/diff
    POST   /api/check                  {alerts?: true}
    GET    /api/config
    PUT    /api/config                 {interval?, proxy?, retries?}
    GET    /api/alerts
    POST   /api/alerts                 {url, name?, format?, events?}
    DELETE /api/alerts/{name}
    POST   /api/alerts/test            {name?}

Anything else is served from the built web UI (SPA fallback to index.html).
The server binds to 127.0.0.1 by default and has no authentication — do not
expose it to untrusted networks.
"""
from __future__ import annotations

import json
import re
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .alerts import SUPPORTED_EVENTS, SUPPORTED_FORMATS, AlertManager
from .monitor import Monitor
from .storage import Storage
from .utils import is_valid_url, normalize_url

WEBUI_DIR = Path(__file__).parent / "webui"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".map": "application/json",
    ".woff2": "font/woff2",
}

FALLBACK_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>PageWatch</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;background:#0b1220;color:#e6edf3;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:560px;padding:2.5rem;background:#111a2b;border:1px solid #24324a;border-radius:12px}
h1{color:#22d3ee;margin-top:0}code{background:#0b1220;padding:.15rem .4rem;border-radius:4px}
a{color:#22d3ee}p{line-height:1.6}
</style></head><body><main>
<h1>PageWatch API is running</h1>
<p>The web dashboard assets are not built in this installation, but the full
<strong>JSON API</strong> is live — try <a href="/api/status"><code>/api/status</code></a>.</p>
<p>To build the dashboard from source:</p>
<p><code>cd apps/web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>then restart <code>pagewatch serve</code>.</p>
</main></body></html>"""

ROUTES = [
    ("GET", re.compile(r"^/api/status$"), "h_status"),
    ("GET", re.compile(r"^/api/watches$"), "h_list_watches"),
    ("POST", re.compile(r"^/api/watches$"), "h_add_watch"),
    ("PATCH", re.compile(r"^/api/watches/([^/]+)$"), "h_update_watch"),
    ("DELETE", re.compile(r"^/api/watches/([^/]+)$"), "h_delete_watch"),
    ("POST", re.compile(r"^/api/watches/([^/]+)/check$"), "h_check_one"),
    ("GET", re.compile(r"^/api/watches/([^/]+)/history$"), "h_history"),
    ("GET", re.compile(r"^/api/watches/([^/]+)/diff$"), "h_diff"),
    ("POST", re.compile(r"^/api/check$"), "h_check_all"),
    ("GET", re.compile(r"^/api/config$"), "h_get_config"),
    ("PUT", re.compile(r"^/api/config$"), "h_put_config"),
    ("GET", re.compile(r"^/api/alerts$"), "h_list_alerts"),
    ("POST", re.compile(r"^/api/alerts$"), "h_add_alert"),
    ("DELETE", re.compile(r"^/api/alerts/([^/]+)$"), "h_delete_alert"),
    ("POST", re.compile(r"^/api/alerts/test$"), "h_test_alerts"),
]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class PagewatchServer(ThreadingHTTPServer):
    """HTTP server carrying the storage/monitor context for handlers."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, storage=None, monitor_factory=None,
                 alert_manager_factory=None, webui_dir=None):
        self.storage = storage or Storage()
        self.monitor_factory = monitor_factory or partial(Monitor, storage=self.storage)
        self.alert_manager_factory = alert_manager_factory or partial(AlertManager, storage=self.storage)
        self.webui_dir = Path(webui_dir) if webui_dir else WEBUI_DIR
        self.write_lock = threading.Lock()
        super().__init__(address, RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = f"pagewatch/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep the terminal clean; errors surface as JSON responses

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Request body must be valid JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return data

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                for route_method, pattern, name in ROUTES:
                    if route_method != method:
                        continue
                    match = pattern.match(path)
                    if match:
                        args = [unquote(g) for g in match.groups()]
                        query = parse_qs(parsed.query)
                        getattr(self, name)(*args, query=query)
                        return
                raise ApiError(404, f"No route for {method} {path}")
            if method == "GET":
                self._serve_static(path)
                return
            raise ApiError(405, "Method not allowed")
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(500, {"error": f"Internal error: {exc}"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # -- static UI -----------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        root = self.server.webui_dir
        index = root / "index.html"
        if not index.is_file():
            body = FALLBACK_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        rel = unquote(path).lstrip("/") or "index.html"
        target = (root / rel).resolve()
        try:
            inside = target.is_relative_to(root.resolve())
        except ValueError:  # pragma: no cover
            inside = False
        if not inside:
            raise ApiError(403, "Forbidden")
        if not target.is_file():
            target = index  # SPA fallback
        body = target.read_bytes()
        content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if target != index:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    # -- validation helpers ----------------------------------------------------

    def _get_watch_or_404(self, name: str) -> dict[str, Any]:
        watch = self.server.storage.get_watch(name)
        if not watch:
            raise ApiError(404, f"Watch '{name}' not found.")
        return watch

    @staticmethod
    def _validate_interval(value) -> int:
        try:
            interval = int(value)
        except (TypeError, ValueError):
            interval = 0
        if interval <= 0:
            raise ApiError(400, "interval must be a positive integer (seconds).")
        return interval

    @staticmethod
    def _validate_patterns(patterns) -> list[str]:
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise ApiError(400, "ignore_patterns must be a list of strings.")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ApiError(400, f"Invalid regex '{pattern}': {exc}")
        return list(patterns)

    # -- handlers --------------------------------------------------------------

    def h_status(self, query=None):
        storage = self.server.storage
        watches = storage.load_watches()
        channels = storage.load_config().get("alerts", {}).get("webhooks", [])
        self._send_json(200, {
            "version": __version__,
            "watch_count": len(watches),
            "alert_channel_count": len(channels),
            "data_dir": str(storage._root),
            "ui_built": (self.server.webui_dir / "index.html").is_file(),
            "alert_formats": list(SUPPORTED_FORMATS),
            "alert_events": list(SUPPORTED_EVENTS),
        })

    def h_list_watches(self, query=None):
        self._send_json(200, self.server.storage.load_watches())

    def h_add_watch(self, query=None):
        body = self._read_json()
        url = normalize_url(str(body.get("url") or ""))
        if not is_valid_url(url):
            raise ApiError(400, f"Invalid URL '{body.get('url')}'")
        name = str(body.get("name") or "").strip()
        if not name:
            name = urlparse(url).netloc.replace(".", "-")
        interval = self._validate_interval(body.get("interval", 3600))
        patterns = self._validate_patterns(body.get("ignore_patterns", []))
        selector = body.get("selector") or None

        with self.server.write_lock:
            if self.server.storage.get_watch(name):
                raise ApiError(409, f"A watch named '{name}' already exists.")
            watch = self.server.storage.add_watch(
                name=name, url=url, selector=selector, interval=interval,
                ignore_patterns=patterns,
            )

        payload = {"watch": watch}
        if body.get("check_now"):
            result = self.server.monitor_factory().check_one(watch)
            payload["result"] = result
            payload["watch"] = self.server.storage.get_watch(name)
        self._send_json(201, payload)

    def h_update_watch(self, name, query=None):
        body = self._read_json()
        with self.server.write_lock:
            watch = self._get_watch_or_404(name)
            changes: dict[str, Any] = {}
            reset_baseline = False

            if "url" in body:
                new_url = normalize_url(str(body["url"] or ""))
                if not is_valid_url(new_url):
                    raise ApiError(400, f"Invalid URL '{body['url']}'")
                changes["url"] = new_url
                reset_baseline = True
            if "selector" in body:
                changes["selector"] = body["selector"] or None
                reset_baseline = True
            if "interval" in body:
                changes["interval"] = self._validate_interval(body["interval"])
            if "ignore_patterns" in body:
                changes["ignore_patterns"] = self._validate_patterns(body["ignore_patterns"])
                reset_baseline = True

            if not changes:
                raise ApiError(400, "No editable fields in request "
                                    "(url, selector, interval, ignore_patterns).")
            if reset_baseline:
                changes["last_hash"] = None
            updated = self.server.storage.update_watch(watch["name"], **changes)
        self._send_json(200, {"watch": updated, "baseline_reset": reset_baseline})

    def h_delete_watch(self, name, query=None):
        with self.server.write_lock:
            if not self.server.storage.remove_watch(name):
                raise ApiError(404, f"Watch '{name}' not found.")
        self._send_json(200, {"removed": name})

    def _run_checks(self, watches: list[dict], with_alerts: bool) -> dict[str, Any]:
        monitor = self.server.monitor_factory()
        results = [monitor.check_one(w) for w in watches]
        deliveries = []
        if with_alerts:
            deliveries = self.server.alert_manager_factory().dispatch(results)
        return {"results": results, "alerts": deliveries}

    def h_check_one(self, name, query=None):
        watch = self._get_watch_or_404(name)
        body = self._read_json()
        payload = self._run_checks([watch], bool(body.get("alerts", True)))
        self._send_json(200, {"result": payload["results"][0], "alerts": payload["alerts"]})

    def h_check_all(self, query=None):
        body = self._read_json()
        watches = self.server.storage.load_watches()
        self._send_json(200, self._run_checks(watches, bool(body.get("alerts", True))))

    def h_history(self, name, query=None):
        self._get_watch_or_404(name)
        snapshot = self.server.storage.load_snapshot(name) or {}
        entries = snapshot.get("history", [])
        try:
            limit = int((query or {}).get("limit", ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 1000))
        latest = snapshot.get("latest", {})
        previous = snapshot.get("previous", {})
        self._send_json(200, {
            "total": len(entries),
            "history": entries[-limit:],
            "latest": {"content_hash": latest.get("content_hash"),
                       "updated_at": latest.get("updated_at")},
            "previous": {"content_hash": previous.get("content_hash"),
                         "updated_at": previous.get("updated_at")},
        })

    def h_diff(self, name, query=None):
        self._get_watch_or_404(name)
        monitor = self.server.monitor_factory()
        diff = monitor.diff(name)
        snapshot = self.server.storage.load_snapshot(name) or {}
        self._send_json(200, {
            "diff": diff,
            "latest_at": snapshot.get("latest", {}).get("updated_at"),
            "previous_at": snapshot.get("previous", {}).get("updated_at"),
        })

    def h_get_config(self, query=None):
        self._send_json(200, self.server.storage.load_config())

    def h_put_config(self, query=None):
        body = self._read_json()
        allowed = {"interval", "proxy", "retries"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(400, f"Unknown config keys: {', '.join(sorted(unknown))}. "
                                f"Editable keys: {', '.join(sorted(allowed))}")
        with self.server.write_lock:
            config = self.server.storage.load_config()
            if "interval" in body:
                config["interval"] = self._validate_interval(body["interval"])
            if "retries" in body:
                try:
                    retries = int(body["retries"])
                except (TypeError, ValueError):
                    retries = -1
                if not 0 <= retries <= 10:
                    raise ApiError(400, "retries must be an integer between 0 and 10.")
                config["retries"] = retries
            if "proxy" in body:
                proxy = body["proxy"]
                if proxy is not None and not isinstance(proxy, str):
                    raise ApiError(400, "proxy must be a string or null.")
                proxy = (proxy or "").strip()
                config["proxy"] = proxy if proxy and proxy.lower() not in ("none", "null") else None
            self.server.storage.save_config(config)
        self._send_json(200, config)

    def h_list_alerts(self, query=None):
        self._send_json(200, self.server.alert_manager_factory().list_channels())

    def h_add_alert(self, query=None):
        body = self._read_json()
        manager = self.server.alert_manager_factory()
        with self.server.write_lock:
            try:
                channel = manager.add_channel(
                    str(body.get("url") or ""),
                    name=(str(body["name"]).strip() if body.get("name") else None),
                    fmt=str(body.get("format") or "generic"),
                    events=str(body.get("events") or "change"),
                )
            except ValueError as exc:
                raise ApiError(400, str(exc))
        self._send_json(201, channel)

    def h_delete_alert(self, name, query=None):
        with self.server.write_lock:
            if not self.server.alert_manager_factory().remove_channel(name):
                raise ApiError(404, f"Alert channel '{name}' not found.")
        self._send_json(200, {"removed": name})

    def h_test_alerts(self, query=None):
        body = self._read_json()
        manager = self.server.alert_manager_factory()
        try:
            deliveries = manager.send_test(body.get("name"))
        except ValueError as exc:
            raise ApiError(404, str(exc))
        self._send_json(200, {"deliveries": deliveries})


def create_server(host: str = "127.0.0.1", port: int = 8787, **kwargs) -> PagewatchServer:
    """Build a PagewatchServer (kwargs: storage, monitor_factory, alert_manager_factory, webui_dir)."""
    return PagewatchServer((host, port), **kwargs)
