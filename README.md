<p align="center">
  <h1 align="center">pagewatch</h1>
  <p align="center">
    <b>Free & Open Source Website Change Monitoring CLI</b><br/>
    Visual diff tracking, change detection, and alerts — all from your terminal.
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/pagewatch/"><img src="https://img.shields.io/pypi/v/pagewatch" alt="PyPI"></a>
  <a href="https://pypi.org/project/pagewatch/"><img src="https://img.shields.io/pypi/dm/pagewatch" alt="Downloads"></a>
  <a href="https://github.com/bobwatch/pagewatch/blob/main/LICENSE"><img src="https://img.shields.io/github/license/bobwatch/pagewatch" alt="License"></a>
  <a href="https://github.com/bobwatch/pagewatch"><img src="https://img.shields.io/github/stars/bobwatch/pagewatch" alt="Stars"></a>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#usage">Usage</a> •
  <a href="#comparison">Comparison</a> •
  <a href="#self-hosting">Self-Hosting</a> •
  <a href="https://pagewatch.tech">pagewatch.tech &rarr;</a>
</p>

---

**pagewatch** is a free, open-source **website change monitor** and **visual diff tool** for developers. Monitor any web page for content changes, get alerted, and keep a full history of every change. It's the most capable **free website change detector** available — and a powerful **Visualping alternative** for teams that want to run their own monitoring infrastructure.

---

## Features

- **CLI-First Design** — Pipe-friendly, scriptable, composable with your existing toolchain
- **Local Web Dashboard** — `pagewatch serve` opens a React dashboard on localhost; data stays in local JSON files
- **CSS Selector Support** — Monitor specific elements, ignore nav bars and footers
- **Content Hashing** — SHA256-based change detection, fast and reliable
- **Unified Diffs** — Color-coded text diffs show exactly what changed
- **Noise Filtering** — Per-watch regex ignore patterns kill false positives from timestamps, counters, and ads
- **Webhook Alerts** — Push change/error notifications to Slack, Discord, Feishu, DingTalk, Telegram, WeCom, Gotify, ntfy, or any JSON endpoint
- **Daemon Mode** — `pagewatch watch` keeps checking continuously, honoring each page's interval
- **Resilient Fetching** — Automatic retries with exponential backoff, optional HTTP(S) proxy
- **JS-Rendered Pages** — Optional Playwright backend renders JavaScript-heavy pages in headless Chromium
- **Scripting-Ready** — `--json` output and `--fail-on-change` exit codes plug straight into cron and CI
- **Backup & Restore** — Export watches and change history as JSON/CSV, re-import with one command
- **JSON Storage** — All data stored locally, easy to inspect and export
- **Change History** — Every check is saved, replay diffs and compare snapshots
- **RSS Feeds** — Subscribe to detected changes in any feed reader, per watch or all merged
- **Simple & Lightweight** — Zero database required, just JSON files on disk

## Quick Start

```bash
# Until the package is published on PyPI, install from the repository:
pip install "git+https://github.com/bobwatch/pagewatch.git"

# Initialize config
pagewatch init

# Add a page to monitor
pagewatch add https://example.com

# Check for changes
pagewatch check

# View your watch list
pagewatch list

# Show diff for a page
pagewatch diff example-com

# Or do it all from the local web dashboard
pagewatch serve
```

> **Tip:** For the hosted experience with a dashboard, visual screenshots, team collaboration, and 99.9% uptime, visit **[pagewatch.tech](https://pagewatch.tech/?ref=github-readme)**.

## Web Dashboard

```bash
pagewatch serve                # opens http://127.0.0.1:8787
pagewatch serve -p 9000 --no-browser
pagewatch serve --token s3cret # require bearer-token auth for /api/*
```

`pagewatch serve` starts a local dashboard (React) plus a JSON REST API
(`/api/*`) on top of the exact same JSON-file storage the CLI uses — add and
edit watches, run checks, read color-coded diffs, browse snapshot history,
and manage alert channels and settings from the browser. No database, no
external services.

**Security notes:**

- The server binds to localhost by default, answers only same-origin
  requests (no CORS headers), and rejects non-localhost `Host` headers
  (DNS-rebinding protection). Keep it on localhost unless you know what
  you're doing.
- Binding to a non-localhost address without `--token` prints a loud
  warning — anyone who can reach the port can read and modify your data.
  With `--token` (or the `PAGEWATCH_TOKEN` env var), every `/api/*` call
  requires `Authorization: Bearer <token>`; the dashboard will prompt for it.
- API responses redact stored secrets (SMTP password, webhook URLs). Backup
  files created by `pagewatch export` still contain per-watch `headers`
  verbatim — they may hold credentials, so store backups accordingly.
- `pagewatch import` and `/api/import` validate every entry (name, URL
  scheme, interval, selector) before writing anything; watch names are
  always sanitized before being used as snapshot file names.

The dashboard ships pre-built inside the package. Its source lives in
[`apps/web`](apps/web/) (React + Vite); see the README there for the dev
workflow.

## Usage

### Add a page to monitor

```bash
pagewatch add https://docs.example.com/api --name api-docs --interval 1800
```

### Monitor a specific element

```bash
pagewatch add https://news.ycombinator.com --name hn-headlines --selector ".titleline"
```

### Monitor a JS-rendered page

Single-page apps and other JavaScript-heavy sites return an empty shell to a
plain HTTP fetch. Enable the optional Playwright backend to render the page in
headless Chromium before extracting text:

```bash
pip install "pagewatch[render] @ git+https://github.com/bobwatch/pagewatch.git"
playwright install chromium

pagewatch add https://spa.example.com/dashboard --name spa-dash --render

# Toggle rendering on an existing watch (resets the baseline)
pagewatch update spa-dash --render        # or --no-render
```

Rendered watches show a `JS` marker in `pagewatch list` and in the dashboard.

### Check all watched pages

```bash
pagewatch check
```

### Check a specific watch

```bash
pagewatch check --name api-docs
```

### Silence noisy elements (ignore patterns)

Pages full of timestamps, view counters, or rotating ads trigger false alerts.
Give a watch regex ignore patterns — matching text lines are dropped before
hashing and diffing:

```bash
pagewatch add https://example.com/pricing --name pricing \
  --ignore "Updated at \d{4}" --ignore "\d+ views" --check-now

# Tune an existing watch
pagewatch update pricing --add-ignore "^Ad:" --remove-ignore "\d+ views"
```

`--check-now` fetches immediately so the baseline is captured at add time.
Editing the URL, selector, or ignore patterns resets the baseline — the next
check re-establishes it without firing a false alert.

### Watch continuously (daemon mode)

```bash
# Keeps running, checks each page on its own interval, alerts on changes
pagewatch watch

# One scheduling pass (useful in scripts/cron), no webhook dispatch
pagewatch watch --once --no-alerts
```

### Get alerted via webhooks

```bash
# Slack incoming webhook
pagewatch alert add https://hooks.slack.com/services/T000/B000/XXXX --name slack-ops --format slack

# Discord, Feishu (Lark), DingTalk, or any JSON endpoint
pagewatch alert add https://discord.com/api/webhooks/... --format discord
pagewatch alert add https://open.feishu.cn/open-apis/bot/v2/hook/... --format feishu
pagewatch alert add https://oapi.dingtalk.com/robot/send?access_token=... --format dingtalk
pagewatch alert add https://example.com/my-endpoint --format generic --events all

# Telegram Bot API — put the chat_id in the URL query
pagewatch alert add "https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>" --format telegram

# Also supported: wecom (WeCom group bot), gotify, ntfy (plain-text publish to a topic URL)
pagewatch alert add "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>" --format wecom
pagewatch alert add "https://gotify.example.com/message?token=<TOKEN>" --format gotify
pagewatch alert add https://ntfy.sh/mytopic --format ntfy

# Verify, inspect, remove
pagewatch alert test
pagewatch alert list
pagewatch alert remove slack-ops
```

Channels subscribe to `change` events (default), `error` events, or `all`.
Every `pagewatch check` and `pagewatch watch` run dispatches alerts automatically (skip with `--no-alerts`).

### Alert only on what matters

Changes are always detected and recorded — an alert filter only gates the
notification. Give a watch a regex and you get alerted only when the diff
matches (use `foo|bar` for several keywords):

```bash
pagewatch add https://example.com/pricing --name pricing --alert-filter "price|discount"
pagewatch update pricing --alert-filter "in stock"   # retune later
pagewatch update pricing --clear-alert-filter        # back to alerting on every change
```

Error alerts can be tamed too: with `error_threshold` a failing watch alerts
once — when its consecutive failures reach the threshold — and sends a single
`recovery` notice (to `events=all` channels and email) when it succeeds again:

```bash
pagewatch config set error_threshold 3   # alert on the 3rd consecutive failure, not every one
```

### Script it (JSON output & exit codes)

```bash
# Machine-readable results + alert delivery reports
pagewatch check --json | jq '.results[] | select(.changed) | .name'

# Exit code 2 when something changed — perfect for cron/CI pipelines
pagewatch check --fail-on-change && echo "nothing changed"

# Inspect a watch's snapshot timeline
pagewatch history pricing --limit 10
```

### Export & restore

```bash
# Full JSON backup of watches + change history (add --include-html for raw snapshots)
pagewatch export > backup.json

# Change history as CSV, straight into your spreadsheet or BI tool
# (CSV is one-way: it contains history rows only and cannot be re-imported)
pagewatch export --format csv -o history.csv

# Restore on another machine (merge by default, --replace to overwrite;
# invalid entries are validated, skipped, and reported)
pagewatch import backup.json

# Migrate from another monitoring tool (same merge/validate rules apply;
# entries pagewatch cannot express, e.g. XPath filters, are skipped with a warning)
pagewatch import changedetection-export.json --from changedetection
pagewatch import distill-export.json --from distill

# Import watches from a plain CSV (columns: name, url, selector, interval, tags)
pagewatch import-csv watches.csv
```

### Proxy & retries

```bash
pagewatch config set proxy http://127.0.0.1:7890   # route checks through a proxy ('none' to clear)
pagewatch config set retries 4                     # retry connection errors and 5xx with backoff
pagewatch config set interval 1800                 # default interval for watches added without --interval
```

### View diff between snapshots

```bash
pagewatch diff api-docs
```

### Subscribe to changes (RSS feed)

```bash
# RSS 2.0 feed of detected changes on stdout — pipe it anywhere, host it statically
pagewatch feed > changes.xml

# A single watch
pagewatch feed api-docs > api-docs.xml

# Or live from the dashboard server (all watches / one watch)
pagewatch serve
#   http://127.0.0.1:8787/feed.xml
#   http://127.0.0.1:8787/feed/api-docs.xml
```

Each feed item carries the unified diff of that change. When the server runs
with `--token`, feed readers can authenticate with
`http://…/feed.xml?token=<token>` instead of a Bearer header — that URL
contains the credential, so only share it with people you trust with full API
access.

### List all watches

```bash
pagewatch list
```

### View configuration

```bash
pagewatch config

# Monitoring statistics and disk usage (check/change counts, largest watches)
pagewatch stats
```

### Remove a watch

```bash
pagewatch remove api-docs
```

### Clone, pause, resume

```bash
pagewatch clone api-docs --new-name api-docs-staging   # duplicate a watch and all its configuration
pagewatch pause api-docs                               # skip it during checks (history is kept)
pagewatch resume api-docs                              # start checking it again
```

## Configuration

All configuration is stored in `~/.pagewatch/` (override the location with the `PAGEWATCH_HOME` environment variable):

```
~/.pagewatch/
  config.json     — global settings (interval, alerts, proxy, retries, error/storage options)
  watches.json    — list of monitored pages (incl. per-watch ignore patterns)
  snapshots/      — per-page snapshot history (JSON)
```

Change settings with `pagewatch config set <key> <value>`:

| Key | Default | Meaning |
|-----|---------|---------|
| `interval` | `3600` | default check interval (seconds) for watches added without `--interval` |
| `retries` | `2` | retry connection errors and 5xx responses with backoff |
| `error_threshold` | `1` | alert only after N consecutive failures; a recovery sends one notice |
| `store_html` | `true` | keep raw HTML in snapshots (`false` shrinks snapshot files) |
| `max_history` | `1000` | cap per-watch snapshot history length |
| `proxy` | unset | route checks through an HTTP(S)/SOCKS5 proxy (`none` clears) |

Alert channels live in `config.json` under `alerts.webhooks`, e.g.:

```json
{
  "interval": 3600,
  "alerts": {
    "webhooks": [
      {"name": "slack-ops", "url": "https://hooks.slack.com/services/...", "format": "slack", "events": "change"}
    ]
  },
  "proxy": null
}
```

## Comparison

| Feature | pagewatch | Visualping | ChangeTower | Distill.io |
|---------|-----------|------------|-------------|------------|
| **Price** | **Free & OSS** | $14+/mo | $19+/mo | $15+/mo |
| **Self-Hosted** | ✅ | ❌ | ❌ | ❌ |
| **CSS Selectors** | ✅ | ✅ | ❌ | ✅ |
| **Visual Diff** | via [pagewatch.tech](https://pagewatch.tech/?ref=github-compare) | ✅ | ❌ | ✅ |
| **CLI** | ✅ | ❌ | ❌ | ❌ |
| **Webhook Alerts** | ✅ Slack/Discord/Feishu/DingTalk/Telegram/WeCom/Gotify/ntfy | ✅ | ✅ | ✅ |
| **Change History** | ✅ | ✅ | ✅ | ✅ |
| **Open Source** | ✅ | ❌ | ❌ | ❌ |
| **Unlimited URLs** | ✅ | ❌ | ❌ | ❌ |
| **Hosted Dashboard** | [pagewatch.tech](https://pagewatch.tech/?ref=github-compare) | ✅ | ✅ | ✅ |

## Self-Hosting

### pip install

```bash
pip install "git+https://github.com/bobwatch/pagewatch.git"
pagewatch init
pagewatch add https://example.com --name mysite --interval 3600
pagewatch check
```

### Run as a cron job

```bash
# Check every hour
0 * * * * /usr/bin/pagewatch check >> /var/log/pagewatch.log 2>&1
```

### Run as a system service

```bash
pagewatch install-service    # systemd --user on Linux, launchd on macOS
pagewatch uninstall-service  # stop and remove it again
```

This installs a per-user service that runs `pagewatch watch` continuously and
restarts it on failure (`~/.config/systemd/user/pagewatch.service` on Linux,
`~/Library/LaunchAgents/tech.pagewatch.daemon.plist` on macOS). On Windows, use
Task Scheduler instead:

```cmd
schtasks /Create /SC HOURLY /TN PageWatch /TR "pagewatch watch"
```

### Docker

```bash
docker run --rm -v $(pwd)/pagewatch-data:/root/.pagewatch bobwatch/pagewatch check
```

## Hosted Version

Love pagewatch but don't want to manage infrastructure? Check out **[pagewatch.tech](https://pagewatch.tech/?ref=github-readme-footer)** — the premium hosted **website change monitoring** platform with:

- Beautiful web dashboard with visual diffs
- 50+ proxy regions worldwide
- Email, Slack, Discord, and webhook alerts
- Cloud-hosted Playwright browsers for JavaScript-heavy pages
- Team collaboration and shared watches
- 99.9% uptime SLA

All CLI commands work the same way — the hosted version adds a powerful web interface on top.

## Repository Layout

```
src/pagewatch/    Python package — CLI, monitor engine, alerts, REST server
src/pagewatch/webui/  Pre-built dashboard assets (generated from apps/web)
apps/web/         React + Vite source of the web dashboard
tests/            pytest suite (CLI, engine, alerts, HTTP API)
```

## License

MIT — see [LICENSE](LICENSE).

---

<p align="center">
  <b><a href="https://pagewatch.tech">pagewatch.tech</a></b> —
  The free website change detector you can trust.
</p>