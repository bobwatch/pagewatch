import functools
from collections.abc import Callable
from datetime import datetime, timezone

from soupsieve import SelectorSyntaxError

from .storage import Storage
from .utils import apply_ignore_patterns, compute_diff, content_hash, extract_text, fetch_page, fetch_page_rendered


class Monitor:
    def __init__(self, storage: Storage | None = None, fetcher: Callable[[str], tuple[str, str]] | None = None,
                 render_fetcher: Callable[[str], tuple[str, str]] | None = None):
        self._store = storage or Storage()
        if fetcher is None:
            config = self._store.load_config()
            try:
                retries = max(0, int(config.get("retries", 2)))
            except (TypeError, ValueError):
                retries = 2
            fetcher = functools.partial(fetch_page, proxy=config.get("proxy") or None, retries=retries)
        self._fetch = fetcher
        self._render_fetch = render_fetcher or fetch_page_rendered

    def check_all(self) -> list[dict]:
        watches = self._store.load_watches()
        results = []
        for w in watches:
            if w.get("paused"):
                continue
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
        extra_headers = watch.get("headers") or {}

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
            fetch = self._render_fetch if watch.get("render") else self._fetch
            html, _ = fetch(url, extra_headers=extra_headers)
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

            self._store.save_snapshot(name, new_hash, text, html, diff=result["diff"])

            watch["last_hash"] = new_hash
            watch["last_checked"] = result["timestamp"]
            watch["check_count"] = (watch.get("check_count") or 0) + 1
            watch["last_status"] = "ok"
            result["consecutive_errors"] = 0
            updates = {
                "last_hash": new_hash,
                "last_checked": result["timestamp"],
                "check_count": watch["check_count"],
                "last_status": "ok",
                "consecutive_errors": 0,
            }
            if watch.get("error_alerted"):
                # The watch had fired an error alert and now succeeds again —
                # flag the recovery so the alert layer can notify once.
                result["error_recovered"] = True
                result["recovered_after"] = watch.get("consecutive_errors") or 0
                updates["error_alerted"] = False
            self._store.update_watch(name, **updates)

        except (OSError, ValueError, RuntimeError, SelectorSyntaxError) as exc:
            result["error"] = str(exc)
            watch["error_count"] = (watch.get("error_count") or 0) + 1
            watch["check_count"] = (watch.get("check_count") or 0) + 1
            watch["last_status"] = "error"
            result["consecutive_errors"] = (watch.get("consecutive_errors") or 0) + 1
            self._store.update_watch(
                name,
                last_checked=result["timestamp"],
                error_count=watch["error_count"],
                check_count=watch["check_count"],
                last_status="error",
                consecutive_errors=result["consecutive_errors"],
            )

        return result

    def diff(self, name: str) -> str | None:
        snapshot = self._store.load_snapshot(name)
        if not snapshot:
            return None
        previous = snapshot.get("previous")
        if not previous:
            return None
        old_text = previous.get("full_text", "")
        new_text = snapshot.get("latest", {}).get("full_text", "")
        return compute_diff(old_text, new_text)