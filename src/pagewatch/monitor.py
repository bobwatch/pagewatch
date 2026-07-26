#!/usr/bin/env python
from datetime import datetime, timezone

from .utils import compute_diff, content_hash, extract_text, fetch_page
from .storage import Storage


class Monitor:
    def __init__(self, storage: Storage | None = None):
        self._store = storage or Storage()

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
            html, _ = fetch_page(url)
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
            self._store.save_watches(self._store.load_watches())

            self._store.save_snapshot(name, new_hash, text, html)

        except Exception as exc:
            result["error"] = str(exc)

        return result

    def diff(self, name: str) -> str | None:
        snapshot = self._store.load_snapshot(name)
        if not snapshot or not snapshot.get("history"):
            return None
        history = snapshot["history"]
        if len(history) < 2:
            return None

        new_snap = history[-1]
        old_snap = history[-2]

        new_text = snapshot.get("latest", {}).get("full_text", "")
        old_text = ""
        if len(history) > 2:
            old_snap = self._store.load_snapshot(name)
            if old_snap and old_snap.get("history"):
                old_text = old_snap.get("latest", {}).get("full_text", "")

        return compute_diff(old_text, new_text)