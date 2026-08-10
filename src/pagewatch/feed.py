"""RSS 2.0 feed generation for detected changes (stdlib only).

Feed items come from snapshot history entries that carry a ``diff`` key —
``Storage.save_snapshot(..., diff=...)`` writes one only when a check found a
real change. Older entries without the key are simply skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
from xml.sax.saxutils import escape

DEFAULT_LIMIT = 50


def _parse_timestamp(ts: Any) -> datetime:
    """Parse an ISO8601 history timestamp; unparsable/naive values sort oldest."""
    try:
        dt = datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_rss(watches_entries: list[dict[str, Any]], channel_title: str, channel_link: str,
              description: str, limit: int = DEFAULT_LIMIT) -> str:
    """Build an RSS 2.0 feed of detected changes.

    ``watches_entries`` is a list of ``{"name": ..., "url": ..., "history": [...]}``
    dicts (one per watch). Items across all watches are merged, sorted newest
    first, and truncated to ``limit``.
    """
    items = []
    for watch in watches_entries:
        name = str(watch.get("name") or "")
        for entry in watch.get("history") or []:
            if "diff" not in entry:
                continue  # no recorded diff = no change (or pre-feed legacy data)
            items.append((name, str(watch.get("url") or channel_link), entry))
    items.sort(key=lambda it: _parse_timestamp(it[2].get("timestamp")), reverse=True)
    items = items[:max(0, int(limit))]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '  <channel>',
        f'    <title>{escape(str(channel_title))}</title>',
        f'    <link>{escape(str(channel_link))}</link>',
        f'    <description>{escape(str(description))}</description>',
    ]
    for name, link, entry in items:
        guid = f"{name}-{entry.get('content_hash') or ''}"
        lines += [
            '    <item>',
            f'      <title>Change detected on {escape(name)}</title>',
            f'      <link>{escape(link)}</link>',
            f'      <guid isPermaLink="false">{escape(guid)}</guid>',
            f'      <pubDate>{format_datetime(_parse_timestamp(entry.get("timestamp")))}</pubDate>',
            f'      <description>{escape(str(entry.get("diff") or ""))}</description>',
            '    </item>',
        ]
    lines += ['  </channel>', '</rss>', '']
    return "\n".join(lines)
