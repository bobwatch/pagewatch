#!/usr/bin/env python
from datetime import datetime, timezone
from typing import Callable

from .storage import Storage
from .utils import compute_diff, content_hash, extract_text, fetch_page


class Monitor:
    """Runs change checks against watched pages.

    A custom ``fetcher`` callable (``url -> (html, final_url)``) may be
    injected, e.g. for testing or for JavaScript-rendering backends.
    """

    def __init__(self, storage: Storage | None = None, fetcher: Callable[[str], tuple[str, str]] | None = None):
        self._store = storage or Storage()
        self._fetch = fetcher or fetch_page

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
        selector = watch.get("selector")

        result = {
            "name": name,
            "url": url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "changed": False,
            "error": None,
            "diff": None,
        }

        try:
            html, _ = self._fetch(url)
            text = extract_text(html, selector)
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
            self._store.update_watch(name, last_hash=new_hash, last_checked=result["timestamp"])

            self._store.save_snapshot(name, new_hash, text, html)

        except Exception as exc:
            result["error"] = str(exc)

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
