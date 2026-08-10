# Changelog

## Unreleased

### Added
- **RSS feeds of detected changes**: `pagewatch feed [name]` prints an RSS 2.0
  feed (all watches merged, or one) to stdout, and `pagewatch serve` exposes
  `GET /feed.xml` and `GET /feed/{name}.xml` (`application/rss+xml`). Each
  item carries the unified diff recorded at check time — snapshot history
  entries now store a capped `diff` when a real change is detected; entries
  written before this version have no diff and are simply skipped. When token
  auth is enabled, the feed endpoints accept `?token=<token>` in addition to
  the Bearer header, since feed readers cannot answer a token prompt.
- **New alert channels**: Telegram Bot API (`--format telegram`, with the
  `chat_id` passed as a query parameter on the webhook URL), WeCom group
  bots (`--format wecom`), Gotify (`--format gotify`), and ntfy
  (`--format ntfy`, published as a plain-text body to the topic URL).
  Available in the CLI, the REST API, and the dashboard format dropdown.

## 0.6.0 — 2026-08-11

### Added
- **JS-rendered page monitoring**: `pagewatch add --render` (and
  `pagewatch update --render/--no-render`) fetches pages with headless
  Chromium via the optional Playwright backend
  (`pip install pagewatch[render] && playwright install chromium`).
  Toggling rendering resets the baseline to avoid false alerts; rendered
  watches show a `JS` marker in `pagewatch list`. The REST API
  (`POST/PATCH /api/watches`, `/api/import`) accepts a boolean `render`
  field, and the dashboard form has a "Render JavaScript" checkbox.
- **Keyword alert filters**: `pagewatch add/update --alert-filter <regex>`
  (and `--clear-alert-filter`) alerts on a change only when the diff matches
  the regex — detection, snapshots, and history are unaffected; filtered
  changes are marked `alert_suppressed` in results and shown as "alert
  filtered" in the CLI. The REST API and import validation accept an
  `alert_filter` field, and the dashboard watch form has an "Alert filter"
  input.
- **Error alert threshold + recovery notices**: `pagewatch config set
  error_threshold N` (default 1) makes a failing watch alert once — when its
  consecutive failures exactly reach `N` — instead of on every failure, and a
  watch that recovers after an alerted failure emits a one-shot `recovery`
  event (delivered to `events=all` channels and email). The dashboard
  settings page exposes the threshold.
- **Snapshot storage controls**: `pagewatch config set store_html false`
  stops storing raw HTML in snapshots (smaller snapshot files;
  `export --include-html` then warns there is no HTML to export), and
  `pagewatch config set max_history N` caps the per-watch history length
  (default 1000). Both are also editable via `PUT /api/config` and the
  dashboard settings page. A new `pagewatch stats` command shows monitoring
  statistics plus disk usage (total, snapshots, largest watches); the
  `/api/stats` response and the dashboard stats page include the same disk
  usage breakdown.
- **System service installation**: `pagewatch install-service` /
  `pagewatch uninstall-service` set up (or remove) a per-user service that
  runs `pagewatch watch` — a systemd user unit on Linux, a launchd agent on
  macOS; unsupported platforms get a Task Scheduler hint. `python -m
  pagewatch` now works as an entry point too.

## 0.5.1 — 2026-08-10

### Security
- **Dashboard CORS removed**: the local server no longer sends
  `Access-Control-Allow-Origin: *`; cross-origin web pages can no longer
  read or drive the API from a browser.
- **DNS-rebinding protection**: when bound to localhost without a token,
  requests with non-localhost `Host` headers are rejected (403).
- **Optional API authentication**: `pagewatch serve --token` (or the
  `PAGEWATCH_TOKEN` env var) requires `Authorization: Bearer <token>` on
  `/api/*`; the dashboard prompts for the token. Serving on a non-localhost
  address without a token now prints a loud warning.
- **Credential redaction**: `/api/config` and the alert-channel endpoints
  mask webhook URLs and the stored SMTP password; submitting a masked URL
  on update keeps the stored one. `pagewatch config` and `pagewatch alert
  list`/`alert add` no longer print secrets in full; `pagewatch export`
  warns when backups contain per-watch `headers` (possible credentials).
- **Import hardening**: `pagewatch import` and `/api/import` validate every
  entry (name, http(s) URL, positive interval, selector syntax, field
  types) before writing. The API import is all-or-nothing with per-entry
  error details; the CLI skips invalid entries with warnings. Watch names
  can no longer traverse out of the snapshots directory (previously a
  crafted backup could write files anywhere).
- **Generic 500 responses**: internal errors no longer leak exception
  details (paths, URLs) to API clients; tracebacks go to stderr instead.

### Fixed
- A single watch with an invalid CSS selector no longer crashes
  `pagewatch check` or kills the `pagewatch watch` daemon; it is recorded
  as an error result. `add`/`update` reject invalid selectors up front.
- `pagewatch add` rejects non-positive intervals (previously a negative
  interval caused hot-loop polling in daemon mode); without `--interval`
  it now honors `config set interval` instead of a hardcoded 3600.
- `pagewatch update --pause/--resume` now applies even when combined with
  other options; `--selector` + `--clear-selector` together is an error;
  the baseline is only reset when ignore patterns actually change.
- `pagewatch watch` picks up watches added while it runs, survives all
  watches being removed (no more `min()` crash), shows paused watches as
  paused, and keeps running when a single check raises unexpectedly.
- `pagewatch list` shows an `error` status; `clone` preserves the paused
  state; auto-generated names for URLs with ports are filesystem-safe
  (no `:` — previously broke snapshot writes on Windows).
- `alert test` with no channels exits 0 with a friendly message instead of
  a misleading "Email not configured" failure; `alert update --url` is
  validated; `config set proxy` validates the scheme.
- CSV import now actually reads the `selector` column, and one malformed
  row no longer aborts the whole import (bad rows are skipped with
  warnings, counts are reported). `export -o` reports I/O errors cleanly
  instead of a raw traceback.
- JSON storage is now thread-safe (instance lock + unique atomic-write
  temp files), fixing lost updates when the dashboard, its scheduler
  thread, and API handlers write concurrently. The dashboard's scheduler
  thread logs and survives unexpected errors instead of dying silently,
  and batch checks no longer hold the write lock during network requests.
- `DELETE /api/watches/{name}/history` also resets the watch's `last_hash`
  and returns 404 when there is no history; `clone` API response shape now
  matches the other endpoints (`{"watch": ...}`); invalid `smtp_port`
  returns 400 instead of 500; index.html no longer gets a long cache
  header on case-insensitive filesystems.

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
