# Changelog

## 0.2.0 — 2026-07-26

### Added
- **Webhook alerts**: new `pagewatch alert add/list/remove/test` command group.
  Channels support `generic`, `slack`, `discord`, `feishu`, and `dingtalk`
  payload formats and subscribe to `change`, `error`, or `all` events.
  `pagewatch check` and `pagewatch watch` dispatch alerts automatically
  (`--no-alerts` to skip).
- **Daemon mode**: `pagewatch watch` runs continuously and checks each page on
  its own interval (`--once` for a single scheduling pass).
- **Export**: `pagewatch export` dumps watches and change history as JSON
  (full backup, `--include-html` optional) or CSV (`--format csv`), to stdout
  or `--output FILE`.
- `PAGEWATCH_HOME` environment variable to relocate the data directory.
- Injectable fetcher in `Monitor` (custom backends, offline testing).
- Test suite (`tests/`) covering utils, storage, monitor, alerts, and CLI.

### Fixed
- Change detection never fired across runs: `last_hash`/`last_checked` were
  not persisted after a check. This is now saved via `Storage.update_watch`.
- `pagewatch diff` compared a snapshot against itself; snapshots now retain
  the previous distinct version (`previous`) so diffs are meaningful.
- Invalid `build-system.build-backend` in `pyproject.toml` broke
  `pip install -e .` (and CI installs). Now `setuptools.build_meta`.
- Unified diffs no longer glue the last removed/added lines together when the
  content has no trailing newline.
- Removed unused imports; `ruff check` passes cleanly.

## 0.1.0 — 2026-07-25

- Initial release: `init`, `add`, `list`, `check`, `diff`, `config`, `remove`
  with CSS selector support, SHA256 change detection, and JSON storage.
