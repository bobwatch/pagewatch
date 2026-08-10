#!/usr/bin/env python
"""Parsers that turn competitor watch-list exports into pagewatch watch dicts.

Both parsers are pure functions: they take the raw export text and return a
``(watches, warnings)`` tuple. ``watches`` is a list of plain dicts with the
same keys a ``pagewatch export`` backup carries (``name``, ``url``,
``selector``, ``interval``, ``tags``), ready for the validation/merge logic
of ``pagewatch import``. Whole-file problems (not JSON, wrong top-level
shape) raise ``ValueError``; broken individual entries are skipped with a
warning instead.

Format assumptions:

- changedetection.io: the watch list (API dump / export) is a JSON object
  keyed by watch UUID. Each value may carry ``url``, ``title``, ``tag``
  (or ``tags``), ``css_filter`` and ``time_between_check``
  (``{weeks, days, hours, minutes, seconds}``). ``css_filter`` holds one
  filter per line; ``css:`` prefixes are stripped, ``xpath:``/``json:``/
  ``jq:`` filters cannot be expressed as a CSS selector and are dropped
  with a warning. Bare lines are treated as CSS selectors.
- Distill.io: "Export → JSON" produces an object with a ``data`` list
  (a ``watches`` list is also accepted). Each monitor has ``name``, ``uri``,
  ``config`` and ``schedule`` — the latter two are JSON-encoded *strings*.
  CSS selections live at ``config.selections[].frames[].includes[]`` with
  ``{"expr": ..., "type": "css"}``; the check interval is
  ``schedule.params.interval`` in seconds. Format confirmed against
  Distill's official export examples, e.g.
  https://distill.io/blog/nvidia-rtx-4090-gpu-stock-tracker/
  Missing/unparseable optional fields are skipped, not fatal.
"""
import json

from .utils import default_watch_name

_INTERVAL_PARTS = (("weeks", 604800), ("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1))


def _load_json(text: str, source: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not a valid {source} export: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"not a valid {source} export: expected a JSON object")  # noqa: TRY004
    return data


def _dedup_name(name: str, url: str, seen: set) -> str | None:
    """Return None (caller skips) when the name was already produced."""
    name = (name or "").strip() or default_watch_name(url)
    if name in seen:
        return None
    seen.add(name)
    return name


def _css_selectors(filters, warnings: list, label: str) -> list[str]:
    """Split foreign filter expressions into CSS selectors, warning about the rest."""
    selectors = []
    for expr in filters:
        expr = (expr or "").strip()
        if not expr:
            continue
        lowered = expr.lower()
        if lowered.startswith("css:"):
            expr = expr[4:].strip()
        elif lowered.startswith(("xpath:", "json:", "jq:")):
            kind = lowered.split(":", 1)[0]
            warnings.append(f"{label}: {kind} filter '{expr}' cannot be converted to a CSS selector, skipped.")
            continue
        if expr and expr not in selectors:
            selectors.append(expr)
    return selectors


def parse_changedetection(text: str) -> tuple[list[dict], list[str]]:
    """Parse a changedetection.io watch list export into pagewatch watch dicts."""
    data = _load_json(text, "changedetection.io")

    watches, warnings, seen = [], [], set()
    for uuid, entry in data.items():
        label = f"watch {uuid}"
        if not isinstance(entry, dict):
            warnings.append(f"{label}: not an object, skipped.")
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            warnings.append(f"{label}: missing URL, skipped.")
            continue
        name = _dedup_name(entry.get("title"), url, seen)
        if name is None:
            warnings.append(f"{label}: duplicate name '{entry.get('title')}', skipped.")
            continue

        watch = {"name": name, "url": url}

        filters = entry.get("css_filter")
        if isinstance(filters, str):
            selectors = _css_selectors(filters.splitlines(), warnings, label)
            if selectors:
                watch["selector"] = ", ".join(selectors)

        tbc = entry.get("time_between_check")
        if isinstance(tbc, dict):
            interval = 0
            for unit, factor in _INTERVAL_PARTS:
                try:
                    interval += int(tbc.get(unit) or 0) * factor
                except (TypeError, ValueError):
                    warnings.append(f"{label}: invalid time_between_check '{unit}', ignored.")
            if interval > 0:
                watch["interval"] = interval

        tags = entry.get("tag") or entry.get("tags")
        if isinstance(tags, str) and tags.strip():
            watch["tags"] = [tags.strip()]
        elif isinstance(tags, list):
            tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
            if tags:
                watch["tags"] = tags

        watches.append(watch)

    return watches, warnings


def _loads_embedded(value, label: str, field: str, warnings: list) -> dict:
    """Distill stores config/schedule as JSON-encoded strings; decode tolerantly."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    if value not in (None, ""):
        warnings.append(f"{label}: unparseable '{field}', ignored.")
    return {}


def parse_distill(text: str) -> tuple[list[dict], list[str]]:
    """Parse a Distill.io "Export → JSON" file into pagewatch watch dicts."""
    data = _load_json(text, "Distill.io")
    monitors = data.get("data") if isinstance(data.get("data"), list) else data.get("watches")
    if not isinstance(monitors, list):
        raise ValueError("not a valid Distill.io export: missing 'data' list of monitors")  # noqa: TRY004

    watches, warnings, seen = [], [], set()
    for i, entry in enumerate(monitors, start=1):
        label = f"monitor #{i}"
        if not isinstance(entry, dict):
            warnings.append(f"{label}: not an object, skipped.")
            continue
        url = entry.get("uri") or entry.get("url")
        if not isinstance(url, str) or not url.strip():
            warnings.append(f"{label}: missing URL, skipped.")
            continue
        name = _dedup_name(entry.get("name"), url, seen)
        if name is None:
            warnings.append(f"{label}: duplicate name '{entry.get('name')}', skipped.")
            continue

        watch = {"name": name, "url": url}

        config = _loads_embedded(entry.get("config"), label, "config", warnings)
        exprs = []
        selections = config.get("selections")
        if isinstance(selections, list):
            for selection in selections:
                if not isinstance(selection, dict):
                    continue
                for frame in selection.get("frames") or []:
                    if not isinstance(frame, dict):
                        continue
                    for include in frame.get("includes") or []:
                        if not isinstance(include, dict):
                            continue
                        expr = include.get("expr")
                        itype = str(include.get("type") or "css").lower()
                        if itype == "css" or not expr:
                            exprs.append(expr)
                        else:
                            warnings.append(f"{label}: {itype} selection '{expr}' cannot be converted "
                                            "to a CSS selector, skipped.")
        selectors = _css_selectors(exprs, warnings, label)
        if selectors:
            watch["selector"] = ", ".join(selectors)

        schedule = _loads_embedded(entry.get("schedule"), label, "schedule", warnings)
        params = schedule.get("params") if isinstance(schedule.get("params"), dict) else {}
        interval = params.get("interval", entry.get("interval"))
        if interval is not None:
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                warnings.append(f"{label}: invalid interval {interval!r}, ignored.")
            else:
                if interval > 0:
                    watch["interval"] = interval

        tags = entry.get("tags")
        if isinstance(tags, list):
            tags = [t.strip() if isinstance(t, str) else (t.get("name") or "").strip()
                    for t in tags if isinstance(t, (str, dict))]
            tags = [t for t in tags if t]
            if tags:
                watch["tags"] = tags

        watches.append(watch)

    return watches, warnings


IMPORTERS = {
    "changedetection": parse_changedetection,
    "distill": parse_distill,
}
