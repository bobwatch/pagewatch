# Changelog

## 0.5.0 — 2026-07-27

### Added
- **Email alerts**: configure SMTP settings via `pagewatch alert email set`
  (`--smtp-host`, `--smtp-port`, `--smtp-user`, `--smtp-pass`, `--to-addrs`,
  `--no-tls`, `--from-addr`). Email is dispatched alongside webhooks for
  change and error events. Also available in the web dashboard under
  "Alert channels > Email alerts (SMTP)".
- **Pause/Resume watches**: `pagewatch pause <name>` and `pagewatch resume
  <name>` temporarily skip a watch during checks without losing its history.
  Paused watches are shown with a muted row in the web dashboard and a
  `paused` status badge in `pagewatch list`.
- **Watch stats**: each watch now tracks `check_count`, `error_count`, and
  `last_status` (ok/error). Displayed in `pagewatch list` and the web
  dashboard table.
- **API endpoints**: `POST /api/watches/{name}/pause`,
  `POST /api/watches/{name}/resume`, `GET /api/alerts/email`,
  `PUT /api/alerts/email`. Status endpoint now returns `paused_count` and
  `email_configured`.
- **CLI `update --pause/--resume`**: pause or resume a watch as part of an
  update command.

## 0.4.0 — 2026-07-26

### Added
- **Local web dashboard**: `pagewatch serve` starts a dependency-free stdlib
  HTTP server exposing a JSON REST API (`/api/*`) over the same disk-file
  storage, and serves a React dashboard — watch CRUD with ignore patterns,
  one-click checks, color-coded diff viewer, snapshot history, alert channel
  management with test delivery, and settings (interval/proxy/retries).
  Options: `--host`, `--port`, `--no-browser`. Localhost by default; no
  authentication — do not expose to untrusted networks.
- **`apps/web`**: React 19 + Vite source of the dashboard. `npm run build`
  emits stable-named assets into `src/pagewatch/webui/`, which ship inside
  the Python package (pip users need no Node.js). `npm run dev` proxies
  `/api` to a running `pagewatch serve` for hot-reload development.
- **CI**: a `Web UI` workflow rebuilds and commits the dashboard assets
  whenever `apps/web/` changes.
- Graceful fallback page when UI assets are missing (API stays available).
- Official brand assets (`apps/web/src/public/`): `logo.svg` as favicon and
  header mark; wordmark SVGs shipped alongside the dashboard.

## 0.3.0 — 2026-07-26

### Added
- **Noise filtering**: per-watch regex ignore patterns (`add --ignore`,
  repeatable) drop matching text lines before hashing/diffing — no more false
  alerts from timestamps, view counters, or rotating ads.
- **`pagewatch update`**: edit a watch's URL, selector, interval, and ignore
  patterns in place (`--add-ignore/--remove-ignore/--clear-ignore`,
  `--clear-selector`). Content-affecting edits reset the baseline so the next
  check re-establishes it without a false alert.
- **Proxy support**: the long-dormant `proxy` config now works — checks route
  through the configured HTTP(S) proxy.
- **Fetch retries**: connection errors, timeouts, and 5xx responses retry
  with exponential backoff (configurable via `config set retries N`, 0–10);
  4xx responses fail fast.
- **`pagewatch config set`**: change `interval`, `proxy`, `retries` from the
  CLI (`proxy none` clears it). Bare `pagewatch config` still prints settings.
- **Scripting outputs**: `check --json` prints machine-readable results plus
  alert delivery reports; `check --fail-on-change` exits with status 2 when
  a change was detected (cron/CI-friendly).
- **`pagewatch history <name>`**: snapshot timeline with current/previous
  markers (`--limit N`).
- **`pagewatch import`**: restore watches and snapshot history from a
  `pagewatch export` backup (merge by default, `--replace` to overwrite).
- **`add --check-now`**: capture the baseline immediately when adding a watch.

### Changed
- Older `config.json` files are transparently upgraded with new default keys
  on load.
- `pagewatch list` shows each watch's ignore-pattern count.

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
