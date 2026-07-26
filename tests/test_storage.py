import tempfile
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
