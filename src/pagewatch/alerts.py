from __future__ import annotations

import base64
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import requests

from .storage import Storage

SUPPORTED_FORMATS = ("generic", "slack", "discord", "feishu", "dingtalk")
SUPPORTED_EVENTS = ("change", "error", "all")
DEFAULT_TIMEOUT = 10
DIFF_PREVIEW_CHARS = 800
WEBHOOK_RETRIES = 3
WEBHOOK_BACKOFF = 1.5


def _obfuscate(plain: str) -> str:
    return base64.b64encode(plain.encode()).decode() if plain else ""


def _deobfuscate(encoded: str) -> str:
    try:
        return base64.b64decode(encoded).decode()
    except (ValueError, OSError):
        return encoded


def render_text(event: dict[str, Any]) -> str:
    kind = event.get("event")
    name = event.get("name")
    url = event.get("url")
    if kind == "change":
        text = f"PageWatch: change detected on '{name}' ({url})"
        preview = (event.get("diff_preview") or "").strip()
        if preview:
            text += "\n```\n" + preview + "\n```"
        return text
    if kind == "error":
        return f"PageWatch: check failed for '{name}' ({url}): {event.get('error')}"
    return "PageWatch: test alert — your webhook channel is working."


def build_payload(fmt: str, event: dict[str, Any]) -> dict[str, Any]:
    text = render_text(event)
    if fmt == "slack":
        return {"text": text}
    if fmt == "discord":
        return {"content": text}
    if fmt == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if fmt == "dingtalk":
        return {"msgtype": "text", "text": {"content": text}}
    payload: dict[str, Any] = {
        "source": "pagewatch",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(event)
    return payload


class AlertManager:
    def __init__(self, storage: Storage | None = None, session: Any = None, timeout: int = DEFAULT_TIMEOUT):
        self._store = storage or Storage()
        self._session = session or requests
        self._timeout = timeout

    # -- email config -------------------------------------------------------

    EMAIL_CONFIG_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_tls", "from_addr", "to_addrs")

    def get_email_config(self) -> dict[str, Any]:
        config = self._store.load_config()
        raw = config.get("alerts", {}).get("email", {})
        if raw.get("smtp_pass_obfuscated"):
            raw["smtp_pass"] = _deobfuscate(raw["smtp_pass_obfuscated"])
        return raw

    def set_email_config(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_pass: str | None = None,
        smtp_tls: bool = True,
        from_addr: str | None = None,
        to_addrs: str | None = None,
    ) -> dict[str, Any]:
        if not smtp_host:
            raise ValueError("SMTP host is required.")
        email_cfg = {
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user or "",
            "smtp_pass_obfuscated": _obfuscate(smtp_pass or ""),
            "smtp_tls": smtp_tls,
            "from_addr": from_addr or smtp_user or "",
            "to_addrs": to_addrs or "",
        }
        config = self._store.load_config()
        config.setdefault("alerts", {})["email"] = email_cfg
        self._store.save_config(config)
        return self.get_email_config()

    def send_email(self, subject: str, body: str) -> dict[str, Any]:
        email_cfg = self.get_email_config()
        report: dict[str, Any] = {"ok": False, "error": None}

        if not email_cfg.get("smtp_host") or not email_cfg.get("to_addrs"):
            report["error"] = "Email not configured."
            return report

        host = email_cfg["smtp_host"]
        port = int(email_cfg.get("smtp_port", 587))
        user = email_cfg.get("smtp_user") or ""
        password = email_cfg.get("smtp_pass") or ""
        use_tls = bool(email_cfg.get("smtp_tls", True))
        from_addr = email_cfg.get("from_addr") or user
        to_addrs = [a.strip() for a in email_cfg.get("to_addrs", "").split(",") if a.strip()]

        if not to_addrs:
            report["error"] = "No recipient addresses configured."
            return report

        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)

        try:
            if use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(host, port, timeout=15) as server:
                    server.starttls(context=context)
                    if user and password:
                        server.login(user, password)
                    server.sendmail(from_addr, to_addrs, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=15) as server:
                    if user and password:
                        server.login(user, password)
                    server.sendmail(from_addr, to_addrs, msg.as_string())
            report["ok"] = True
        except (smtplib.SMTPException, OSError) as exc:
            report["error"] = str(exc)

        return report

    def dispatch_email_event(self, event: dict[str, Any]) -> dict[str, Any]:
        email_cfg = self.get_email_config()
        report: dict[str, Any] = {"channel": "email", "event": event.get("event"), "ok": False, "error": None}
        if not email_cfg.get("smtp_host") or not email_cfg.get("to_addrs"):
            report["error"] = "Email not configured"
            return report

        kind = event.get("event", "change")
        name = event.get("name", "?")
        url = event.get("url", "?")

        if kind == "change":
            subject = f"[PageWatch] Change detected: {name}"
            body = f"Change detected on '{name}'\n\nURL: {url}\nTime: {event.get('timestamp', '?')}\n"
            preview = (event.get("diff_preview") or "").strip()
            if preview:
                body += f"\nDiff preview:\n{preview}\n"
        elif kind == "error":
            subject = f"[PageWatch] Check failed: {name}"
            body = f"Check failed for '{name}'\n\nURL: {url}\nError: {event.get('error', '?')}\nTime: {event.get('timestamp', '?')}\n"
        else:
            subject = "[PageWatch] Test notification"
            body = "This is a test notification from PageWatch. Your email alerts are working."

        result = self.send_email(subject, body)
        report.update(result)
        return report

    # -- channel management -------------------------------------------------

    def list_channels(self) -> list[dict[str, Any]]:
        config = self._store.load_config()
        return list(config.get("alerts", {}).get("webhooks", []))

    def add_channel(
        self,
        url: str,
        name: str | None = None,
        fmt: str = "generic",
        events: str = "change",
    ) -> dict[str, Any]:
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format '{fmt}'. Choose from: {', '.join(SUPPORTED_FORMATS)}")
        if events not in SUPPORTED_EVENTS:
            raise ValueError(f"Unsupported events filter '{events}'. Choose from: {', '.join(SUPPORTED_EVENTS)}")
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Webhook URL must start with http:// or https://")

        config = self._store.load_config()
        webhooks = config.setdefault("alerts", {}).setdefault("webhooks", [])

        existing_names = {c.get("name") for c in webhooks}
        if name is None:
            i = 1
            while f"webhook-{i}" in existing_names:
                i += 1
            name = f"webhook-{i}"
        elif name in existing_names:
            raise ValueError(f"Alert channel '{name}' already exists.")

        channel = {"name": name, "url": url, "format": fmt, "events": events}
        webhooks.append(channel)
        self._store.save_config(config)
        return channel

    def update_channel(self, name: str, **kwargs) -> dict[str, Any] | None:
        config = self._store.load_config()
        webhooks = config.setdefault("alerts", {}).setdefault("webhooks", [])
        for c in webhooks:
            if c.get("name") == name:
                if "url" in kwargs:
                    c["url"] = kwargs["url"]
                if "fmt" in kwargs:
                    c["format"] = kwargs["fmt"]
                if "format" in kwargs:
                    c["format"] = kwargs["format"]
                if "events" in kwargs:
                    c["events"] = kwargs["events"]
                self._store.save_config(config)
                return c
        return None

    def remove_channel(self, name: str) -> bool:
        config = self._store.load_config()
        webhooks = config.get("alerts", {}).get("webhooks", [])
        filtered = [c for c in webhooks if c.get("name") != name]
        if len(filtered) == len(webhooks):
            return False
        config["alerts"]["webhooks"] = filtered
        self._store.save_config(config)
        return True

    def channels_for(self, event_kind: str) -> list[dict[str, Any]]:
        return [
            c for c in self.list_channels()
            if c.get("events", "change") == "all" or c.get("events", "change") == event_kind
        ]

    # -- dispatch ------------------------------------------------------------

    def dispatch(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deliveries: list[dict[str, Any]] = []
        for result in results:
            if result.get("paused"):
                continue
            if result.get("changed"):
                kind = "change"
            elif result.get("error"):
                kind = "error"
            else:
                continue

            event: dict[str, Any] = {
                "event": kind,
                "name": result.get("name"),
                "url": result.get("url"),
                "timestamp": result.get("timestamp"),
            }
            if kind == "change":
                diff = result.get("diff") or ""
                event["diff_preview"] = diff[:DIFF_PREVIEW_CHARS] if diff else None
                event["content_hash"] = result.get("current_hash")
            else:
                event["error"] = result.get("error")

            for channel in self.channels_for(kind):
                d = self._post(channel, event)
                deliveries.append(d)
                self._store.append_alert_event({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "watch": result.get("name"),
                    "event": kind,
                    "channel": channel.get("name"),
                    "ok": d["ok"],
                    "error": d.get("error"),
                })

            email_d = self.dispatch_email_event(event)
            deliveries.append(email_d)
            self._store.append_alert_event({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "watch": result.get("name"),
                "event": kind,
                "channel": "email",
                "ok": email_d["ok"],
                "error": email_d.get("error"),
            })
        return deliveries

    def send_test(self, name: str | None = None) -> list[dict[str, Any]]:
        channels = self.list_channels()
        if name is not None:
            channels = [c for c in channels if c.get("name") == name]
            if not channels:
                raise ValueError(f"Alert channel '{name}' not found.")
        event = {
            "event": "test",
            "name": "pagewatch",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        deliveries = [self._post(c, event) for c in channels]
        if name is None:
            deliveries.append(self.dispatch_email_event(event))
        return deliveries

    def _post(self, channel: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        payload = build_payload(channel.get("format", "generic"), event)
        report = {
            "channel": channel.get("name"),
            "url": channel.get("url"),
            "event": event.get("event"),
            "ok": False,
            "status": None,
            "error": None,
        }
        last_error: str | None = None
        for attempt in range(WEBHOOK_RETRIES):
            try:
                resp = self._session.post(channel["url"], json=payload, timeout=self._timeout)
                report["status"] = getattr(resp, "status_code", None)
                report["ok"] = report["status"] is not None and 200 <= report["status"] < 300
                if report["ok"]:
                    return report
                if report["status"] is not None and 400 <= report["status"] < 500:
                    report["error"] = f"HTTP {report['status']} (not retried)"
                    return report
                last_error = f"HTTP {report['status']}"
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = str(exc)
            except (requests.RequestException, OSError) as exc:
                report["error"] = str(exc)
                return report
            if attempt < WEBHOOK_RETRIES - 1:
                time.sleep(WEBHOOK_BACKOFF * (2 ** attempt))
        report["error"] = last_error
        return report