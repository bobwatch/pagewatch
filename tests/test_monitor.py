import tempfile
from contextlib import contextmanager
from pathlib import Path

from pagewatch.monitor import Monitor
from pagewatch.storage import Storage


class SeqFetcher:
    """Fake fetcher returning queued pages (or raising queued exceptions)."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(page, Exception):
            raise page
        return page, url


@contextmanager
def env(*pages):
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        yield store, Monitor(storage=store, fetcher=SeqFetcher(*pages))


PAGE_V1 = "<html><body><p>version one</p></body></html>"
PAGE_V2 = "<html><body><p>version two</p></body></html>"


def test_first_check_persists_state_without_change():
    with env(PAGE_V1) as (store, monitor):
        store.add_watch("w", "https://x.test")
        result = monitor.check_one(store.get_watch("w"))
        assert result["error"] is None
        assert result["changed"] is False
        assert result["current_hash"]

        # Regression: last_hash/last_checked must be persisted to disk,
        # otherwise change detection can never trigger on later runs.
        reloaded = store.get_watch("w")
        assert reloaded["last_hash"] == result["current_hash"]
        assert reloaded["last_checked"] is not None

        snap = store.load_snapshot("w")
        assert snap["latest"]["full_text"] == "version one"


def test_change_detected_with_diff_and_hash_update():
    with env(PAGE_V1, PAGE_V2) as (store, monitor):
        store.add_watch("w", "https://x.test")
        first = monitor.check_one(store.get_watch("w"))
        second = monitor.check_one(store.get_watch("w"))

        assert second["changed"] is True
        assert second["diff"]
        assert "-version one" in second["diff"]
        assert "+version two" in second["diff"]
        assert store.get_watch("w")["last_hash"] == second["current_hash"]
        assert second["current_hash"] != first["current_hash"]


def test_no_change_on_identical_content():
    with env(PAGE_V1, PAGE_V1) as (store, monitor):
        store.add_watch("w", "https://x.test")
        monitor.check_one(store.get_watch("w"))
        result = monitor.check_one(store.get_watch("w"))
        assert result["changed"] is False
        assert result["diff"] is None


def test_fetch_error_is_captured():
    with env(PAGE_V1, RuntimeError("boom"), PAGE_V1) as (store, monitor):
        store.add_watch("w", "https://x.test")
        ok = monitor.check_one(store.get_watch("w"))
        assert ok["error"] is None
        failed = monitor.check_one(store.get_watch("w"))
        assert failed["error"] == "boom"
        assert failed["changed"] is False
        # State from the successful run must survive a failed fetch.
        assert store.get_watch("w")["last_hash"] == ok["current_hash"]


def test_check_all_covers_every_watch():
    with env(PAGE_V1) as (store, monitor):
        store.add_watch("a", "https://a.test")
        store.add_watch("b", "https://b.test")
        results = monitor.check_all()
        assert {r["name"] for r in results} == {"a", "b"}


def test_diff_uses_previous_distinct_snapshot():
    with env(PAGE_V1, PAGE_V2) as (store, monitor):
        store.add_watch("w", "https://x.test")
        monitor.check_one(store.get_watch("w"))
        assert monitor.diff("w") is None  # only one distinct snapshot so far
        monitor.check_one(store.get_watch("w"))
        diff = monitor.diff("w")
        assert diff is not None
        assert "+version two" in diff
        assert "-version one" in diff
