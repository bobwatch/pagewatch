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

MAX_HISTORY = 1000


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _safe_json_load(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Name must not be empty.")
    if len(name) > 128:
        raise ValueError("Name must be at most 128 characters.")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Name must not contain path separators.")
    return name


class Storage:
    def __init__(self, path: Path | None = None):
        self._root = path or data_dir()
        self._root.mkdir(parents=True, exist_ok=True)
        self._config_file = self._root / "config.json"
        self._watches_file = self._root / "watches.json"
        self._snapshots_dir = self._root / "snapshots"
        self._snapshots_dir.mkdir(exist_ok=True)

    def load_config(self) -> dict[str, Any]:
        config = copy.deepcopy(DEFAULT_CONFIG)
        loaded = _safe_json_load(self._config_file)
        if isinstance(loaded, dict):
            config.update(loaded)
        return config

    def save_config(self, config: dict[str, Any]) -> None:
        _atomic_write(self._config_file, json.dumps(config, indent=2, ensure_ascii=False))

    def load_watches(self) -> list[dict[str, Any]]:
        data = _safe_json_load(self._watches_file)
        return data if isinstance(data, list) else []

    def save_watches(self, watches: list[dict[str, Any]]) -> None:
        _atomic_write(self._watches_file, json.dumps(watches, indent=2, ensure_ascii=False))

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
        paused: bool = False,
    ) -> dict[str, Any]:
        name = _validate_name(name)
        watches = self.load_watches()
        watch = {
            "name": name,
            "url": url,
            "selector": selector,
            "interval": interval,
            "ignore_patterns": list(ignore_patterns or []),
            "paused": paused,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_checked": None,
            "last_hash": None,
            "check_count": 0,
            "error_count": 0,
            "last_status": None,
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
        return _safe_json_load(snap_file)

    def restore_snapshot(self, name: str, data: dict[str, Any]) -> None:
        snap_file = self._snapshots_dir / f"{name}.json"
        _atomic_write(snap_file, json.dumps(data, indent=2, ensure_ascii=False))

    def save_snapshot(self, name: str, content_hash: str, full_text: str, html: str) -> dict[str, Any]:
        snap_file = self._snapshots_dir / f"{name}.json"
        existing = self.load_snapshot(name) or {"history": []}
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "text_length": len(full_text),
        }
        history = existing.setdefault("history", [])
        history.append(entry)

        if len(history) > MAX_HISTORY:
            history[:len(history) - MAX_HISTORY] = []

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
        _atomic_write(snap_file, json.dumps(existing, indent=2, ensure_ascii=False))
        return entry