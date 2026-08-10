import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from pagewatch.storage import Storage


@contextmanager
def tmp_storage():
    with tempfile.TemporaryDirectory() as tmp:
        yield Storage(Path(tmp))


def test_config_defaults_and_roundtrip():
    with tmp_storage() as store:
        cfg = store.load_config()
        assert cfg == {"interval": 3600, "alerts": {}, "proxy": None, "retries": 2}
        cfg["interval"] = 60
        cfg["alerts"] = {"webhooks": [{"name": "x", "url": "https://h", "format": "generic", "events": "all"}]}
        store.save_config(cfg)
        assert store.load_config() == cfg


def test_config_merges_defaults_into_legacy_files():
    with tmp_storage() as store:
        # A config written before new keys existed gains them on load.
        store._config_file.write_text('{"interval": 120, "alerts": {}, "proxy": null}', encoding="utf-8")
        cfg = store.load_config()
        assert cfg["interval"] == 120
        assert cfg["retries"] == 2


def test_load_config_returns_isolated_copies():
    with tmp_storage() as store:
        first = store.load_config()
        first["alerts"].setdefault("webhooks", []).append({"name": "x"})
        second = store.load_config()
        assert second["alerts"] == {}


def test_add_get_and_list_watch():
    with tmp_storage() as store:
        assert store.load_watches() == []
        store.add_watch("site", "https://example.com", selector=".main", interval=120)
        watches = store.load_watches()
        assert len(watches) == 1
        w = store.get_watch("site")
        assert w is not None
        assert w["url"] == "https://example.com"
        assert w["selector"] == ".main"
        assert w["interval"] == 120
        assert w["last_hash"] is None
        assert w["ignore_patterns"] == []
        assert store.get_watch("nope") is None


def test_add_watch_persists_ignore_patterns():
    with tmp_storage() as store:
        store.add_watch("site", "https://example.com", ignore_patterns=[r"\d+ views"])
        assert store.get_watch("site")["ignore_patterns"] == [r"\d+ views"]


def test_restore_snapshot_roundtrip():
    with tmp_storage() as store:
        doc = {
            "history": [{"timestamp": "t1", "content_hash": "h1", "text_length": 3}],
            "latest": {"content_hash": "h1", "full_text": "abc", "updated_at": "t1"},
        }
        store.restore_snapshot("site", doc)
        assert store.load_snapshot("site") == doc


def test_watch_name_validation():
    with tmp_storage() as store:
        try:
            store.add_watch("", "https://x.test")
            raise AssertionError("expected ValueError for empty name")
        except ValueError:
            pass
        try:
            store.add_watch("a" * 129, "https://x.test")
            raise AssertionError("expected ValueError for long name")
        except ValueError:
            pass
        try:
            store.add_watch("../etc", "https://x.test")
            raise AssertionError("expected ValueError for path separator")
        except ValueError:
            pass


def test_update_watch_persists():
    with tmp_storage() as store:
        store.add_watch("site", "https://example.com")
        updated = store.update_watch("site", last_hash="abc", last_checked="2026-01-01T00:00:00+00:00")
        assert updated["last_hash"] == "abc"
        # A fresh read from disk must see the update.
        assert store.get_watch("site")["last_hash"] == "abc"
        assert store.update_watch("missing", last_hash="x") is None


def test_remove_watch_and_snapshot_cleanup():
    with tmp_storage() as store:
        store.add_watch("site", "https://example.com")
        store.save_snapshot("site", "h1", "text", "<html></html>")
        assert store.load_snapshot("site") is not None
        assert store.remove_watch("site") is True
        assert store.load_watches() == []
        assert store.load_snapshot("site") is None
        assert store.remove_watch("site") is False


def test_snapshot_history_and_previous_tracking():
    with tmp_storage() as store:
        store.save_snapshot("s", "h1", "text one", "<html>1</html>")
        snap = store.load_snapshot("s")
        assert len(snap["history"]) == 1
        assert snap["latest"]["content_hash"] == "h1"
        assert "previous" not in snap

        store.save_snapshot("s", "h2", "text two", "<html>2</html>")
        snap = store.load_snapshot("s")
        assert len(snap["history"]) == 2
        assert snap["latest"]["content_hash"] == "h2"
        assert snap["previous"]["content_hash"] == "h1"
        assert snap["previous"]["full_text"] == "text one"

        # Saving the same content again must not clobber the previous version.
        store.save_snapshot("s", "h2", "text two", "<html>2</html>")
        snap = store.load_snapshot("s")
        assert len(snap["history"]) == 3
        assert snap["previous"]["content_hash"] == "h1"


def test_add_watch_rejects_reserved_and_control_characters():
    with tmp_storage() as store:
        for bad in ("a:b", "a|b", "a<b", 'a"b', "a?b", "a*b", "a\tb", "a\x01b"):
            try:
                store.add_watch(bad, "https://x.test")
                raise AssertionError(f"expected ValueError for name {bad!r}")
            except ValueError:
                pass
        assert store.load_watches() == []


def test_restore_snapshot_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = Storage(root / "data")
        for bad in ("../../evil", "..\\..\\evil"):
            try:
                store.restore_snapshot(bad, {"history": []})
                raise AssertionError(f"expected ValueError for name {bad!r}")
            except ValueError:
                pass
        # Nothing must have been written outside the data directory.
        assert sorted(p.name for p in root.iterdir()) == ["data"]


def test_save_snapshot_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = Storage(root / "data")
        try:
            store.save_snapshot("../../evil", "h1", "text", "<html></html>")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert sorted(p.name for p in root.iterdir()) == ["data"]


def test_concurrent_update_watch_keeps_counts_and_file_intact():
    with tmp_storage() as store:
        store.add_watch("w", "https://x.test")
        threads_count, increments = 4, 25

        def bump():
            for _ in range(increments):
                # Read-modify-write must hold the instance lock across both
                # calls, otherwise the increment itself races.
                with store._lock:
                    w = store.get_watch("w")
                    store.update_watch("w", check_count=w["check_count"] + 1)

        threads = [threading.Thread(target=bump) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert store.get_watch("w")["check_count"] == threads_count * increments
        # The file must still be valid JSON (no torn concurrent writes).
        assert isinstance(store.load_watches(), list)
        # No leftover tmp files from atomic writes.
        leftovers = [p for p in store._root.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
