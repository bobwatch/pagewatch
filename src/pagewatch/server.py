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
The server binds to 127.0.0.1 by default. An optional bearer token (the
``token`` argument or the ``PAGEWATCH_TOKEN`` environment variable) protects
all ``/api/*`` endpoints; without it, only loopback Host headers are accepted.
"""
from __future__ import annotations

import copy
import hmac
import ipaddress
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .alerts import SUPPORTED_EVENTS, SUPPORTED_FORMATS, AlertManager
from .monitor import Monitor
from .storage import Storage, _validate_name
from .utils import is_valid_url, normalize_url, validate_selector

MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
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
    ("GET", re.compile(r"^/api/stats$"), "h_stats"),
    ("GET", re.compile(r"^/api/watches$"), "h_list_watches"),
    ("POST", re.compile(r"^/api/watches$"), "h_add_watch"),
    ("PATCH", re.compile(r"^/api/watches/([^/]+)$"), "h_update_watch"),
    ("DELETE", re.compile(r"^/api/watches/([^/]+)$"), "h_delete_watch"),
    ("POST", re.compile(r"^/api/watches/([^/]+)/check$"), "h_check_one"),
    ("POST", re.compile(r"^/api/watches/([^/]+)/pause$"), "h_pause_watch"),
    ("POST", re.compile(r"^/api/watches/([^/]+)/resume$"), "h_resume_watch"),
    ("POST", re.compile(r"^/api/watches/([^/]+)/clone$"), "h_clone_watch"),
    ("GET", re.compile(r"^/api/watches/([^/]+)/history$"), "h_history"),
    ("DELETE", re.compile(r"^/api/watches/([^/]+)/history$"), "h_delete_history"),
    ("GET", re.compile(r"^/api/watches/([^/]+)/diff$"), "h_diff"),
    ("POST", re.compile(r"^/api/check$"), "h_check_all"),
    ("POST", re.compile(r"^/api/batch/pause$"), "h_batch_pause"),
    ("POST", re.compile(r"^/api/batch/resume$"), "h_batch_resume"),
    ("POST", re.compile(r"^/api/batch/delete$"), "h_batch_delete"),
    ("POST", re.compile(r"^/api/batch/check$"), "h_batch_check"),
    ("GET", re.compile(r"^/api/config$"), "h_get_config"),
    ("PUT", re.compile(r"^/api/config$"), "h_put_config"),
    ("GET", re.compile(r"^/api/alerts$"), "h_list_alerts"),
    ("POST", re.compile(r"^/api/alerts$"), "h_add_alert"),
    ("PATCH", re.compile(r"^/api/alerts/([^/]+)$"), "h_update_alert"),
    ("DELETE", re.compile(r"^/api/alerts/([^/]+)$"), "h_delete_alert"),
    ("POST", re.compile(r"^/api/alerts/test$"), "h_test_alerts"),
    ("GET", re.compile(r"^/api/alerts/email$"), "h_get_email_config"),
    ("PUT", re.compile(r"^/api/alerts/email$"), "h_set_email_config"),
    ("GET", re.compile(r"^/api/alerts/history$"), "h_alerts_history"),
    ("GET", re.compile(r"^/api/export$"), "h_export"),
    ("POST", re.compile(r"^/api/import$"), "h_import"),
    ("POST", re.compile(r"^/api/daemon/start$"), "h_daemon_start"),
    ("POST", re.compile(r"^/api/daemon/stop$"), "h_daemon_stop"),
    ("GET", re.compile(r"^/api/daemon/status$"), "h_daemon_status"),
]


class ApiError(Exception):
    def __init__(self, status: int, message: str, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload


def _mask_url(url: str) -> str:
    """Mask a webhook URL to ``scheme://host/…`` so secrets in the path stay server-side."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/…"
    return "***"


def _host_without_port(host_header: str) -> str:
    host_header = host_header.strip()
    if host_header.startswith("["):  # [::1]:8787
        end = host_header.find("]")
        return host_header[1:end] if end != -1 else host_header
    if ":" in host_header:
        return host_header.rsplit(":", 1)[0]
    return host_header


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _validate_import_watch(entry: Any) -> list[str]:
    """Validate one watch from an import payload; returns a list of problems (empty = ok)."""
    if not isinstance(entry, dict):
        return ["entry must be a JSON object"]
    errors: list[str] = []
    try:
        _validate_name(str(entry.get("name") or ""))
    except ValueError as exc:
        errors.append(f"name: {exc}")
    if not is_valid_url(str(entry.get("url") or "")):
        errors.append(f"url: invalid URL {entry.get('url')!r} (http/https required)")
    interval = entry.get("interval")
    if interval is not None and (isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0):
        errors.append("interval: must be a positive integer (seconds)")
    tags = entry.get("tags")
    if tags is not None and (not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)):
        errors.append("tags: must be a list of strings")
    headers = entry.get("headers")
    if headers is not None and (
        not isinstance(headers, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
    ):
        errors.append("headers: must be an object mapping strings to strings")
    selector = entry.get("selector")
    if selector:
        try:
            validate_selector(str(selector))
        except ValueError as exc:
            errors.append(f"selector: {exc}")
    return errors


class PagewatchServer(ThreadingHTTPServer):
    """HTTP server carrying the storage/monitor context for handlers."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, storage=None, monitor_factory=None,
                 alert_manager_factory=None, webui_dir=None, token=None):
        self.storage = storage or Storage()
        self.monitor_factory = monitor_factory or partial(Monitor, storage=self.storage)
        self.alert_manager_factory = alert_manager_factory or partial(AlertManager, storage=self.storage)
        self.webui_dir = Path(webui_dir) if webui_dir else WEBUI_DIR
        # Reuse the storage RLock so multi-step read→modify→write handlers stay
        # atomic against the storage layer's own per-method locking.
        self.write_lock = self.storage._lock
        self.token = token or os.environ.get("PAGEWATCH_TOKEN") or None
        self.bind_is_loopback = _is_loopback_host(str(address[0]))
        self._daemon_thread: threading.Thread | None = None
        self._daemon_stop = threading.Event()
        super().__init__(address, RequestHandler)
        if not self.bind_is_loopback and not self.token:
            print(
                "WARNING: pagewatch is binding to a non-loopback address with NO access token — "
                "anyone who can reach this port has full control. Set PAGEWATCH_TOKEN or pass --token.",
                file=sys.stderr,
            )

    def start_daemon(self) -> None:
        if self._daemon_thread and self._daemon_thread.is_alive():
            return
        self._daemon_stop.clear()
        self._daemon_thread = threading.Thread(target=self._daemon_loop, daemon=True)
        self._daemon_thread.start()

    def stop_daemon(self) -> None:
        self._daemon_stop.set()
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5)

    @property
    def daemon_running(self) -> bool:
        return self._daemon_thread is not None and self._daemon_thread.is_alive()

    def _daemon_loop(self) -> None:
        while not self._daemon_stop.is_set():
            try:
                self._daemon_tick()
            except Exception:  # noqa: BLE001 - the daemon must survive any per-round failure
                print("pagewatch daemon: check round failed:", file=sys.stderr)
                traceback.print_exc()
            self._daemon_stop.wait(30)

    def _daemon_tick(self) -> None:
        """One check round: run every watch whose interval has elapsed."""
        now = time.time()
        watches = self.storage.load_watches()
        for w in watches:
            if w.get("paused"):
                continue
            last = w.get("last_checked")
            try:
                interval = int(w.get("interval") or 3600)
            except (TypeError, ValueError):
                interval = 3600
            if last:
                try:
                    ts = datetime.fromisoformat(last).timestamp()
                    if now < ts + interval:
                        continue
                except (ValueError, TypeError):
                    pass
            monitor = self.monitor_factory()
            result = monitor.check_one(w)
            am = self.alert_manager_factory()
            am.dispatch([result])


class RequestHandler(BaseHTTPRequestHandler):
    server_version = f"pagewatch/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------

    def log_message(self, format, *args):
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
        if length > MAX_BODY_SIZE:
            raise ApiError(413, f"Request body too large (max {MAX_BODY_SIZE // 1024 // 1024} MB).")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Request body must be valid JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return data

    def _check_access(self, path: str) -> None:
        server = self.server
        # DNS-rebinding guard: an unauthenticated server bound to loopback only
        # answers requests addressed to localhost — browsers block cross-origin
        # reads, but a hostile page could otherwise rebind a domain to 127.0.0.1.
        if not server.token and server.bind_is_loopback:
            hostname = _host_without_port(self.headers.get("Host") or "")
            if hostname.lower() not in ("localhost", "127.0.0.1", "::1"):
                raise ApiError(403, "Forbidden Host header.")
        if server.token and path.startswith("/api/"):
            auth = self.headers.get("Authorization") or ""
            if not hmac.compare_digest(auth, f"Bearer {server.token}"):
                raise ApiError(401, "Unauthorized: provide 'Authorization: Bearer <token>'.")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            self._check_access(path)
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
            self._send_json(exc.status, exc.payload or {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001 - last-resort 500; details go to stderr, never to the client
            print(f"pagewatch server: unhandled error for {method} {path}:", file=sys.stderr)
            traceback.print_exc()
            self._send_json(500, {"error": "Internal server error"})

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

    def do_OPTIONS(self):
        # The dashboard is same-origin with the API, so no CORS: no ACAO
        # headers anywhere, and preflight requests get a plain 405.
        self._send_json(405, {"error": "Method not allowed"})

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
        if target != index.resolve():
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
        paused_count = sum(1 for w in watches if w.get("paused"))
        email_cfg = storage.load_config().get("alerts", {}).get("email", {})
        all_tags = sorted({t for w in watches for t in (w.get("tags") or [])})
        self._send_json(200, {
            "version": __version__,
            "watch_count": len(watches),
            "paused_count": paused_count,
            "alert_channel_count": len(channels),
            "email_configured": bool(email_cfg.get("smtp_host") or email_cfg.get("smtp_pass_obfuscated")),
            "data_dir": str(storage._root),
            "ui_built": (self.server.webui_dir / "index.html").is_file(),
            "alert_formats": list(SUPPORTED_FORMATS),
            "alert_events": list(SUPPORTED_EVENTS),
            "daemon_running": self.server.daemon_running,
            "tags": all_tags,
        })

    def h_stats(self, query=None):
        self._send_json(200, self.server.storage.get_stats())

    def h_list_watches(self, query=None):
        watches = self.server.storage.load_watches()
        search = ((query or {}).get("search") or [None])[0]
        tag = ((query or {}).get("tag") or [None])[0]
        if search:
            search = search.lower()
            watches = [w for w in watches if search in w["name"].lower() or search in w["url"].lower()]
        if tag:
            watches = [w for w in watches if tag in (w.get("tags") or [])]
        self._send_json(200, watches)

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
        tags = body.get("tags") or []
        headers = body.get("headers") or {}

        with self.server.write_lock:
            if self.server.storage.get_watch(name):
                raise ApiError(409, f"A watch named '{name}' already exists.")
            watch = self.server.storage.add_watch(
                name=name, url=url, selector=selector, interval=interval,
                ignore_patterns=patterns, tags=tags, headers=headers,
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
            if "tags" in body:
                changes["tags"] = list(body["tags"])
            if "headers" in body:
                changes["headers"] = dict(body["headers"])
                reset_baseline = True

            if not changes:
                raise ApiError(400, "No editable fields in request "
                                    "(url, selector, interval, ignore_patterns, tags, headers).")
            if reset_baseline:
                changes["last_hash"] = None
            updated = self.server.storage.update_watch(watch["name"], **changes)
        self._send_json(200, {"watch": updated, "baseline_reset": reset_baseline})

    def h_delete_watch(self, name, query=None):
        with self.server.write_lock:
            if not self.server.storage.remove_watch(name):
                raise ApiError(404, f"Watch '{name}' not found.")
        self._send_json(200, {"removed": name})

    def h_pause_watch(self, name, query=None):
        with self.server.write_lock:
            self._get_watch_or_404(name)
            self.server.storage.update_watch(name, paused=True)
        self._send_json(200, {"name": name, "paused": True})

    def h_resume_watch(self, name, query=None):
        with self.server.write_lock:
            self._get_watch_or_404(name)
            self.server.storage.update_watch(name, paused=False)
        self._send_json(200, {"name": name, "paused": False})

    def h_clone_watch(self, name, query=None):
        body = self._read_json()
        new_name = str(body.get("name") or "").strip() or f"{name}-copy"
        with self.server.write_lock:
            watch = self._get_watch_or_404(name)
            if self.server.storage.get_watch(new_name):
                raise ApiError(409, f"A watch named '{new_name}' already exists.")
            cloned = self.server.storage.add_watch(
                name=new_name, url=watch["url"], selector=watch.get("selector"),
                interval=watch.get("interval", 3600),
                ignore_patterns=list(watch.get("ignore_patterns") or []),
                tags=list(watch.get("tags") or []),
                headers=dict(watch.get("headers") or {}),
            )
        self._send_json(201, {"watch": cloned})

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
        config = copy.deepcopy(self.server.storage.load_config())
        email = config.get("alerts", {}).get("email", {})
        if email.get("smtp_pass_obfuscated"):
            email["smtp_pass_obfuscated"] = "***"
        if email.get("smtp_pass"):
            email["smtp_pass"] = "***"
        for channel in config.get("alerts", {}).get("webhooks", []):
            if channel.get("url"):
                channel["url"] = _mask_url(channel["url"])
        self._send_json(200, config)

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
        channels = self.server.alert_manager_factory().list_channels()
        masked = [
            {**c, "url": _mask_url(c["url"])} if c.get("url") else dict(c)
            for c in channels
        ]
        self._send_json(200, masked)

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

    def h_update_alert(self, name, query=None):
        body = self._read_json()
        if not body:
            raise ApiError(400, "No fields to update (url, format, events)")
        manager = self.server.alert_manager_factory()
        with self.server.write_lock:
            old = next((c for c in manager.list_channels() if c.get("name") == name), None)
            kwargs = {}
            if "url" in body:
                new_url = str(body["url"] or "")
                # The API serves masked URLs; never write a mask (or an empty
                # value) back over the stored URL — treat it as "keep as is".
                if new_url and "***" not in new_url and new_url != _mask_url((old or {}).get("url", "")):
                    kwargs["url"] = new_url
            if "format" in body:
                fmt = str(body["format"])
                if fmt not in SUPPORTED_FORMATS:
                    raise ApiError(400, f"Unsupported format '{fmt}'")
                kwargs["format"] = fmt
            if "events" in body:
                ev = str(body["events"])
                if ev not in SUPPORTED_EVENTS:
                    raise ApiError(400, f"Unsupported events '{ev}'")
                kwargs["events"] = ev
            channel = manager.update_channel(name, **kwargs) if kwargs else old
            if not channel:
                raise ApiError(404, f"Alert channel '{name}' not found.")
        if channel.get("url"):
            channel = {**channel, "url": _mask_url(channel["url"])}
        self._send_json(200, channel)

    def h_get_email_config(self, query=None):
        cfg = self.server.alert_manager_factory().get_email_config()
        safe = {k: v for k, v in cfg.items() if k != "smtp_pass" and k != "smtp_pass_obfuscated"}
        safe["smtp_pass_set"] = bool(cfg.get("smtp_pass") or cfg.get("smtp_pass_obfuscated"))
        self._send_json(200, safe)

    def h_set_email_config(self, query=None):
        body = self._read_json()
        manager = self.server.alert_manager_factory()
        try:
            smtp_port = int(body.get("smtp_port", 587))
        except (TypeError, ValueError):
            raise ApiError(400, "smtp_port must be an integer.")
        with self.server.write_lock:
            try:
                cfg = manager.set_email_config(
                    smtp_host=str(body.get("smtp_host", "")),
                    smtp_port=smtp_port,
                    smtp_user=str(body.get("smtp_user") or ""),
                    smtp_pass=str(body.get("smtp_pass") or ""),
                    smtp_tls=bool(body.get("smtp_tls", True)),
                    from_addr=str(body.get("from_addr") or ""),
                    to_addrs=str(body.get("to_addrs") or ""),
                )
            except ValueError as exc:
                raise ApiError(400, str(exc))
        safe = {k: v for k, v in cfg.items() if k != "smtp_pass" and k != "smtp_pass_obfuscated"}
        safe["smtp_pass_set"] = bool(cfg.get("smtp_pass") or cfg.get("smtp_pass_obfuscated"))
        self._send_json(200, safe)

    def h_export(self, query=None):
        storage = self.server.storage
        watches = storage.load_watches()
        snapshots = {}
        for w in watches:
            snap = storage.load_snapshot(w["name"])
            if snap:
                latest = dict(snap.get("latest", {}))
                latest.pop("html", None)
                entry = {"history": snap.get("history", []), "latest": latest}
                if snap.get("previous"):
                    entry["previous"] = snap["previous"]
                snapshots[w["name"]] = entry
        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "pagewatch_version": __version__,
            "watches": watches,
            "snapshots": snapshots,
        }
        self._send_json(200, data)

    def h_import(self, query=None):
        body = self._read_json()
        storage = self.server.storage
        watches = body.get("watches")
        if not isinstance(watches, list):
            raise ApiError(400, "Invalid backup: missing 'watches' list.")
        # All-or-nothing: validate every entry before touching storage.
        incoming, problems = [], []
        for i, entry in enumerate(watches):
            errors = _validate_import_watch(entry)
            if errors:
                problems.append({
                    "index": i,
                    "name": entry.get("name") if isinstance(entry, dict) else None,
                    "errors": errors,
                })
            else:
                incoming.append(entry)
        if problems:
            raise ApiError(400, "Import validation failed; nothing was imported.",
                           {"error": "Import validation failed; nothing was imported.",
                            "details": problems})
        snapshots = body.get("snapshots") or {}
        replace = bool(body.get("replace"))
        with self.server.write_lock:
            if replace:
                for w in storage.load_watches():
                    storage.remove_watch(w["name"])
                storage.save_watches(incoming)
                imported = [w["name"] for w in incoming]
                skipped = []
            else:
                current = storage.load_watches()
                existing_names = {w["name"] for w in current}
                imported, skipped = [], []
                for w in incoming:
                    if w["name"] in existing_names:
                        skipped.append(w["name"])
                        continue
                    current.append(w)
                    imported.append(w["name"])
                storage.save_watches(current)
            restored = 0
            for wname in imported:
                snap = snapshots.get(wname)
                if isinstance(snap, dict) and snap.get("history"):
                    storage.restore_snapshot(wname, snap)
                    restored += 1
        self._send_json(200, {"imported": len(imported), "skipped": len(skipped), "restored": restored})

    def h_daemon_start(self, query=None):
        self.server.start_daemon()
        self._send_json(200, {"running": True})

    def h_daemon_stop(self, query=None):
        self.server.stop_daemon()
        self._send_json(200, {"running": False})

    def h_daemon_status(self, query=None):
        self._send_json(200, {"running": self.server.daemon_running})

    def h_delete_history(self, name, query=None):
        with self.server.write_lock:
            self._get_watch_or_404(name)
            snap_file = self.server.storage._snapshots_dir / f"{name}.json"
            if not snap_file.is_file():
                raise ApiError(404, f"No history for watch '{name}'.")
            snap_file.unlink()
            self.server.storage.update_watch(name, last_hash=None)
        self._send_json(200, {"removed": name})

    def _batch_op(self, op: str, query=None):
        body = self._read_json()
        names = body.get("names", [])
        if not isinstance(names, list) or not names:
            raise ApiError(400, "Provide a 'names' list.")
        results = []
        targets = []
        with self.server.write_lock:
            for n in names:
                w = self.server.storage.get_watch(n)
                if not w:
                    results.append({"name": n, "ok": False, "error": "not found"})
                    continue
                if op == "check":
                    targets.append(w)
                elif op == "pause":
                    self.server.storage.update_watch(n, paused=True)
                    results.append({"name": n, "ok": True})
                elif op == "resume":
                    self.server.storage.update_watch(n, paused=False)
                    results.append({"name": n, "ok": True})
                elif op == "delete":
                    self.server.storage.remove_watch(n)
                    results.append({"name": n, "ok": True})
        # Network checks run outside the write lock; check_one persists
        # through storage methods that lock individually.
        if targets:
            monitor = self.server.monitor_factory()
            for w in targets:
                try:
                    r = monitor.check_one(w)
                    results.append({"name": w["name"], "ok": True, "result": r})
                except Exception as exc:  # noqa: BLE001 - one failing check must not fail the whole batch
                    results.append({"name": w["name"], "ok": False, "error": str(exc)})
        self._send_json(200, {"results": results})

    def h_batch_pause(self, query=None):
        self._batch_op("pause", query)

    def h_batch_resume(self, query=None):
        self._batch_op("resume", query)

    def h_batch_delete(self, query=None):
        self._batch_op("delete", query)

    def h_batch_check(self, query=None):
        self._batch_op("check", query)

    def h_alerts_history(self, query=None):
        history = self.server.storage.load_alerts_history()
        limit = 100
        self._send_json(200, {"total": len(history), "history": history[-limit:]})


def create_server(host: str = "127.0.0.1", port: int = 8787, token: str | None = None,
                  **kwargs) -> PagewatchServer:
    """Build a PagewatchServer (kwargs: storage, monitor_factory, alert_manager_factory, webui_dir).

    ``token`` enables bearer-token auth on ``/api/*``; when omitted, the
    ``PAGEWATCH_TOKEN`` environment variable is used as a fallback.
    """
    return PagewatchServer((host, port), token=token, **kwargs)
