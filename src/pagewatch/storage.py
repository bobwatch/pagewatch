import copy
import json
import os
import threading
import uuid
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
MAX_ALERT_HISTORY = 1000


def _atomic_write(path: Path, data: str) -> None:
    # Unique tmp name per call: a fixed "path.tmp" would race when several
    # threads write the same file concurrently.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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
    if any(c in '<>"|?*:' or ord(c) < 32 for c in name):
        raise ValueError("Name must not contain control or reserved characters.")
    return name


class Storage:
    def __init__(self, path: Path | None = None):
        self._lock = threading.RLock()
        self._root = path or data_dir()
        self._root.mkdir(parents=True, exist_ok=True)
        self._config_file = self._root / "config.json"
        self._watches_file = self._root / "watches.json"
        self._snapshots_dir = self._root / "snapshots"
        self._snapshots_dir.mkdir(exist_ok=True)
        self._alerts_history_file = self._root / "alerts_history.json"

    def load_config(self) -> dict[str, Any]:
        with self._lock:
            config = copy.deepcopy(DEFAULT_CONFIG)
            loaded = _safe_json_load(self._config_file)
            if isinstance(loaded, dict):
                config.update(loaded)
            return config

    def save_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            _atomic_write(self._config_file, json.dumps(config, indent=2, ensure_ascii=False))

    def load_watches(self) -> list[dict[str, Any]]:
        with self._lock:
            data = _safe_json_load(self._watches_file)
            return data if isinstance(data, list) else []

    def save_watches(self, watches: list[dict[str, Any]]) -> None:
        with self._lock:
            _atomic_write(self._watches_file, json.dumps(watches, indent=2, ensure_ascii=False))

    def get_watch(self, name: str) -> dict[str, Any] | None:
        with self._lock:
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
        tags: list[str] | None = None,
        headers: dict[str, str] | None = None,
        render: bool = False,
    ) -> dict[str, Any]:
        name = _validate_name(name)
        with self._lock:
            watches = self.load_watches()
            watch = {
                "name": name,
                "url": url,
                "selector": selector,
                "interval": interval,
                "ignore_patterns": list(ignore_patterns or []),
                "tags": list(tags or []),
                "headers": dict(headers or {}),
                "paused": paused,
                "render": bool(render),
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
        _validate_name(name)
        with self._lock:
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
        with self._lock:
            watches = self.load_watches()
            for w in watches:
                if w["name"] == name:
                    w.update(kwargs)
                    self.save_watches(watches)
                    return w
            return None

    def load_snapshot(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            snap_file = self._snapshots_dir / f"{name}.json"
            return _safe_json_load(snap_file)

    def restore_snapshot(self, name: str, data: dict[str, Any]) -> None:
        name = _validate_name(name)
        with self._lock:
            snap_file = self._snapshots_dir / f"{name}.json"
            _atomic_write(snap_file, json.dumps(data, indent=2, ensure_ascii=False))

    def save_snapshot(self, name: str, content_hash: str, full_text: str, html: str) -> dict[str, Any]:
        name = _validate_name(name)
        with self._lock:
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

    # -- alerts history -------------------------------------------------------

    def load_alerts_history(self) -> list[dict[str, Any]]:
        with self._lock:
            data = _safe_json_load(self._alerts_history_file)
            return data if isinstance(data, list) else []

    def save_alerts_history(self, history: list[dict[str, Any]]) -> None:
        with self._lock:
            if len(history) > MAX_ALERT_HISTORY:
                history[:len(history) - MAX_ALERT_HISTORY] = []
            _atomic_write(self._alerts_history_file, json.dumps(history, indent=2, ensure_ascii=False))

    def append_alert_event(self, entry: dict[str, Any]) -> None:
        with self._lock:
            history = self.load_alerts_history()
            history.append(entry)
            self.save_alerts_history(history)

    # -- stats ----------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            watches = self.load_watches()
            total = len(watches)
            active = sum(1 for w in watches if w.get("last_hash") and not w.get("paused"))
            paused = sum(1 for w in watches if w.get("paused"))
            errored = sum(1 for w in watches if w.get("last_status") == "error")
            total_checks = sum(w.get("check_count", 0) for w in watches)
            total_errors = sum(w.get("error_count", 0) for w in watches)

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            week_ago = datetime.now(timezone.utc).timestamp() - 7 * 86400
            month_ago = datetime.now(timezone.utc).timestamp() - 30 * 86400
            today_changes = 0
            week_changes = 0
            month_changes = 0

            for w in watches:
                snap = self.load_snapshot(w["name"])
                if not snap:
                    continue
                for entry in snap.get("history", []):
                    ts = entry.get("timestamp", "")
                    try:
                        t = datetime.fromisoformat(ts).timestamp()
                    except (ValueError, TypeError):
                        continue
                    if ts.startswith(today):
                        today_changes += 1
                    if t >= week_ago:
                        week_changes += 1
                    if t >= month_ago:
                        month_changes += 1

            top_changed = sorted(
                [(w["name"], len((self.load_snapshot(w["name"]) or {}).get("history", []))) for w in watches],
                key=lambda x: x[1], reverse=True
            )[:5]

            return {
                "total_watches": total,
                "active_watches": active,
                "paused_watches": paused,
                "errored_watches": errored,
                "total_checks": total_checks,
                "total_errors": total_errors,
                "error_rate": round(total_errors / max(total_checks, 1) * 100, 1),
                "changes_today": today_changes,
                "changes_week": week_changes,
                "changes_month": month_changes,
                "top_changed": [{"name": n, "snapshots": c} for n, c in top_changed],
            }
