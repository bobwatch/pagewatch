#!/usr/bin/env python
import functools
from datetime import datetime, timezone
from typing import Callable

from .storage import Storage
from .utils import apply_ignore_patterns, compute_diff, content_hash, extract_text, fetch_page


class Monitor:
    """Runs change checks against watched pages.

    A custom ``fetcher`` callable (``url -> (html, final_url)``) may be
    injected, e.g. for testing or for JavaScript-rendering backends. When no
    fetcher is given, one is built from the stored config (proxy, retries).
    """

    def __init__(self, storage: Storage | None = None, fetcher: Callable[[str], tuple[str, str]] | None = None):
        self._store = storage or Storage()
        if fetcher is None:
            config = self._store.load_config()
            try:
                retries = max(0, int(config.get("retries", 2)))
            except (TypeError, ValueError):
                retries = 2
            fetcher = functools.partial(fetch_page, proxy=config.get("proxy") or None, retries=retries)
        self._fetch = fetcher

    def check_all(self) -> list[dict]:
        watches = self._store.load_watches()
        results = []
        for w in watches:
            result = self.check_one(w)
            results.append(result)
        return results

    def check_one(self, watch: dict) -> dict:
        name = watch["name"]
        url = watch["url"]

        if watch.get("paused"):
            return {
                "name": name,
                "url": url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "changed": False,
                "error": None,
                "diff": None,
                "paused": True,
            }

        selector = watch.get("selector")

        result = {
            "name": name,
            "url": url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "changed": False,
            "error": None,
            "diff": None,
            "paused": False,
        }

        try:
            html, _ = self._fetch(url)
            text = extract_text(html, selector)
            text = apply_ignore_patterns(text, watch.get("ignore_patterns"))
            new_hash = content_hash(text)

            prev_hash = watch.get("last_hash")
            result["current_hash"] = new_hash

            if prev_hash and prev_hash != new_hash:
                snapshot = self._store.load_snapshot(name)
                if snapshot and snapshot.get("latest", {}).get("full_text"):
                    old_text = snapshot["latest"]["full_text"]
                    result["diff"] = compute_diff(old_text, text)
                result["changed"] = True

            watch["last_hash"] = new_hash
            watch["last_checked"] = result["timestamp"]
            watch["check_count"] = (watch.get("check_count") or 0) + 1
            watch["last_status"] = "ok"
            self._store.update_watch(
                name,
                last_hash=new_hash,
                last_checked=result["timestamp"],
                check_count=watch["check_count"],
                last_status="ok",
            )

            self._store.save_snapshot(name, new_hash, text, html)

        except Exception as exc:
            result["error"] = str(exc)
            watch["error_count"] = (watch.get("error_count") or 0) + 1
            watch["check_count"] = (watch.get("check_count") or 0) + 1
            watch["last_status"] = "error"
            self._store.update_watch(
                name,
                last_checked=result["timestamp"],
                error_count=watch["error_count"],
                check_count=watch["check_count"],
                last_status="error",
            )

        return result

    def diff(self, name: str) -> str | None:
        """Return the diff between the two most recent distinct snapshots."""
        snapshot = self._store.load_snapshot(name)
        if not snapshot:
            return None
        previous = snapshot.get("previous")
        if not previous:
            return None
        old_text = previous.get("full_text", "")
        new_text = snapshot.get("latest", {}).get("full_text", "")
        return compute_diff(old_text, new_text)
