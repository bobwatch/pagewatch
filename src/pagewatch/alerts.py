#!/usr/bin/env python
"""Webhook alert channels for pagewatch.

Channels are stored in ``config.json`` under ``alerts.webhooks``::

    {
      "alerts": {
        "webhooks": [
          {"name": "ops", "url": "https://hooks.slack.com/...",
           "format": "slack", "events": "change"}
        ]
      }
    }

Supported payload formats: ``generic`` (full JSON event), ``slack``,
``discord``, ``feishu`` (Lark), and ``dingtalk``. Channels can subscribe to
``change`` events, ``error`` events, or ``all``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from .storage import Storage

SUPPORTED_FORMATS = ("generic", "slack", "discord", "feishu", "dingtalk")
SUPPORTED_EVENTS = ("change", "error", "all")
DEFAULT_TIMEOUT = 10
DIFF_PREVIEW_CHARS = 800


def render_text(event: dict[str, Any]) -> str:
    """Render a human-readable one-line (plus optional diff) message."""
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
    """Build the provider-specific JSON payload for an event."""
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
    """Manages webhook channels and dispatches alert events to them."""

    def __init__(self, storage: Storage | None = None, session: Any = None, timeout: int = DEFAULT_TIMEOUT):
        self._store = storage or Storage()
        self._session = session or requests
        self._timeout = timeout

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
        """Send alerts for check results (changes and errors). Returns delivery reports."""
        deliveries: list[dict[str, Any]] = []
        for result in results:
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
                deliveries.append(self._post(channel, event))
        return deliveries

    def send_test(self, name: str | None = None) -> list[dict[str, Any]]:
        """Send a test event to one named channel, or to all channels."""
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
        return [self._post(c, event) for c in channels]

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
        try:
            resp = self._session.post(channel["url"], json=payload, timeout=self._timeout)
            report["status"] = getattr(resp, "status_code", None)
            report["ok"] = report["status"] is not None and 200 <= report["status"] < 300
            if not report["ok"]:
                report["error"] = f"HTTP {report['status']}"
        except Exception as exc:
            report["error"] = str(exc)
        return report
