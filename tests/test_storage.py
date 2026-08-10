import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from pagewatch.storage import MAX_HISTORY, Storage


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


def test_store_html_disabled_stores_empty_html_but_keeps_text():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp), store_html=False)
        store.save_snapshot("s", "h1", "text one", "<html>1</html>")
        store.save_snapshot("s", "h2", "text two", "<html>2</html>")
        snap = store.load_snapshot("s")
        assert snap["latest"]["html"] == ""
        assert snap["latest"]["full_text"] == "text two"
        # Diff inputs (full_text / previous) are unaffected.
        assert snap["previous"]["full_text"] == "text one"
        assert snap["latest"]["content_hash"] == "h2"
        assert len(snap["history"]) == 2


def test_store_html_default_keeps_html():
    with tmp_storage() as store:
        store.save_snapshot("s", "h1", "text", "<html>1</html>")
        assert store.load_snapshot("s")["latest"]["html"] == "<html>1</html>"


def test_max_history_trims_history():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp), max_history=3)
        for i in range(5):
            store.save_snapshot("s", f"h{i}", f"text {i}", "<html></html>")
        history = store.load_snapshot("s")["history"]
        assert len(history) == 3
        assert [e["content_hash"] for e in history] == ["h2", "h3", "h4"]


def test_storage_reads_store_html_and_max_history_from_config():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        cfg = store.load_config()
        cfg["store_html"] = False
        cfg["max_history"] = 2
        store.save_config(cfg)
        # A fresh Storage() with no explicit args honors the config file.
        configured = Storage(Path(tmp))
        assert configured._store_html is False
        assert configured._max_history == 2
        configured.save_snapshot("s", "h1", "t1", "<html>1</html>")
        assert configured.load_snapshot("s")["latest"]["html"] == ""


def test_storage_invalid_max_history_in_config_falls_back():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        cfg = store.load_config()
        cfg["max_history"] = -5
        store.save_config(cfg)
        assert Storage(Path(tmp))._max_history == MAX_HISTORY


def test_get_disk_usage():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        store.save_config(store.load_config())
        store.save_watches([])
        store.save_snapshot("big", "h1", "x" * 500, "<html></html>")
        store.save_snapshot("small", "h2", "y", "<html></html>")

        usage = store.get_disk_usage()
        big_size = (Path(tmp) / "snapshots" / "big.json").stat().st_size
        small_size = (Path(tmp) / "snapshots" / "small.json").stat().st_size
        other = (Path(tmp) / "config.json").stat().st_size + (Path(tmp) / "watches.json").stat().st_size

        assert usage["snapshots_bytes"] == big_size + small_size
        assert usage["total_bytes"] == big_size + small_size + other
        assert [w["name"] for w in usage["per_watch"]] == ["big", "small"]
        assert usage["per_watch"][0]["bytes"] == big_size


def test_get_disk_usage_empty():
    with tmp_storage() as store:
        usage = store.get_disk_usage()
        assert usage["snapshots_bytes"] == 0
        assert usage["per_watch"] == []
        assert usage["total_bytes"] == 0
