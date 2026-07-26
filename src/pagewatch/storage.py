#!/usr/bin/env python
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import data_dir

DEFAULT_CONFIG: dict[str, Any] = {
    "interval": 3600,
    "alerts": {},
    "proxy": None,
    "retries": 2,
}


class Storage:
    def __init__(self, path: Path | None = None):
        self._root = path or data_dir()
        self._root.mkdir(parents=True, exist_ok=True)
        self._config_file = self._root / "config.json"
        self._watches_file = self._root / "watches.json"
        self._snapshots_dir = self._root / "snapshots"
        self._snapshots_dir.mkdir(exist_ok=True)

    def load_config(self) -> dict[str, Any]:
        """Load config, merging defaults so older config files gain new keys."""
        config = copy.deepcopy(DEFAULT_CONFIG)
        if self._config_file.is_file():
            loaded = json.loads(self._config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update(loaded)
        return config

    def save_config(self, config: dict[str, Any]) -> None:
        self._config_file.write_text(
            json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def load_watches(self) -> list[dict[str, Any]]:
        if self._watches_file.is_file():
            return json.loads(self._watches_file.read_text(encoding="utf-8"))
        return []

    def save_watches(self, watches: list[dict[str, Any]]) -> None:
        self._watches_file.write_text(
            json.dumps(watches, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_watch(self, name: str) -> dict[str, Any] | None:
        for w in self.load_watches():
            if w["name"] == name:
                return w
        return None

    def add_watch(
        self,
        name: str,
        url: str,
        selector: str | None = None,
        interval: int = 3600,
        ignore_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        watches = self.load_watches()
        watch = {
            "name": name,
            "url": url,
            "selector": selector,
            "interval": interval,
            "ignore_patterns": list(ignore_patterns or []),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_checked": None,
            "last_hash": None,
        }
        watches.append(watch)
        self.save_watches(watches)
        return watch

    def remove_watch(self, name: str) -> bool:
        watches = self.load_watches()
        filtered = [w for w in watches if w["name"] != name]
        if len(filtered) == len(watches):
            return False
        self.save_watches(filtered)
        snap_file = self._snapshots_dir / f"{name}.json"
        if snap_file.is_file():
            snap_file.unlink()
        return True

    def update_watch(self, name: str, **kwargs) -> dict[str, Any] | None:
        watches = self.load_watches()
        for w in watches:
            if w["name"] == name:
                w.update(kwargs)
                self.save_watches(watches)
                return w
        return None

    def load_snapshot(self, name: str) -> dict[str, Any] | None:
        snap_file = self._snapshots_dir / f"{name}.json"
        if snap_file.is_file():
            return json.loads(snap_file.read_text(encoding="utf-8"))
        return None

    def restore_snapshot(self, name: str, data: dict[str, Any]) -> None:
        """Write a full snapshot document (e.g. from a backup) as-is."""
        snap_file = self._snapshots_dir / f"{name}.json"
        snap_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def save_snapshot(self, name: str, content_hash: str, full_text: str, html: str) -> dict[str, Any]:
        snap_file = self._snapshots_dir / f"{name}.json"
        existing = self.load_snapshot(name) or {"history": []}
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "text_length": len(full_text),
        }
        existing.setdefault("history", []).append(entry)

        # Preserve the outgoing snapshot so diffs can always compare the two
        # most recent distinct versions of the page.
        prev_latest = existing.get("latest")
        if prev_latest and prev_latest.get("content_hash") != content_hash:
            existing["previous"] = {
                "content_hash": prev_latest.get("content_hash"),
                "full_text": prev_latest.get("full_text", ""),
                "updated_at": prev_latest.get("updated_at"),
            }

        existing["latest"] = {
            "content_hash": content_hash,
            "full_text": full_text,
            "html": html,
            "updated_at": entry["timestamp"],
        }
        snap_file.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return entry
