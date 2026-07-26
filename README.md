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
- **CSS Selector Support** — Monitor specific elements, ignore nav bars and footers
- **Content Hashing** — SHA256-based change detection, fast and reliable
- **Unified Diffs** — Color-coded text diffs show exactly what changed
- **Noise Filtering** — Per-watch regex ignore patterns kill false positives from timestamps, counters, and ads
- **Webhook Alerts** — Push change/error notifications to Slack, Discord, Feishu, DingTalk, or any JSON endpoint
- **Daemon Mode** — `pagewatch watch` keeps checking continuously, honoring each page's interval
- **Resilient Fetching** — Automatic retries with exponential backoff, optional HTTP(S) proxy
- **Scripting-Ready** — `--json` output and `--fail-on-change` exit codes plug straight into cron and CI
- **Backup & Restore** — Export watches and change history as JSON/CSV, re-import with one command
- **JSON Storage** — All data stored locally, easy to inspect and export
- **Change History** — Every check is saved, replay diffs and compare snapshots
- **Simple & Lightweight** — Zero database required, just JSON files on disk

## Quick Start

```bash
pip install pagewatch

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
```

> **Tip:** For the hosted experience with a dashboard, visual screenshots, team collaboration, and 99.9% uptime, visit **[pagewatch.tech](https://pagewatch.tech/?ref=github-readme)**.

## Usage

### Add a page to monitor

```bash
pagewatch add https://docs.example.com/api --name api-docs --interval 1800
```

### Monitor a specific element

```bash
pagewatch add https://news.ycombinator.com --name hn-headlines --selector ".titleline"
```

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

# Verify, inspect, remove
pagewatch alert test
pagewatch alert list
pagewatch alert remove slack-ops
```

Channels subscribe to `change` events (default), `error` events, or `all`.
Every `pagewatch check` and `pagewatch watch` run dispatches alerts automatically (skip with `--no-alerts`).

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
pagewatch export --format csv -o history.csv

# Restore on another machine (merge by default, --replace to overwrite)
pagewatch import backup.json
```

### Proxy & retries

```bash
pagewatch config set proxy http://127.0.0.1:7890   # route checks through a proxy ('none' to clear)
pagewatch config set retries 4                     # retry connection errors and 5xx with backoff
pagewatch config set interval 1800                 # default interval for new setups
```

### View diff between snapshots

```bash
pagewatch diff api-docs
```

### List all watches

```bash
pagewatch list
```

### View configuration

```bash
pagewatch config
```

### Remove a watch

```bash
pagewatch remove api-docs
```

## Configuration

All configuration is stored in `~/.pagewatch/` (override the location with the `PAGEWATCH_HOME` environment variable):

```
~/.pagewatch/
  config.json     — global settings (interval, alert webhooks, proxy, retries)
  watches.json    — list of monitored pages (incl. per-watch ignore patterns)
  snapshots/      — per-page snapshot history (JSON)
```

Change settings with `pagewatch config set <key> <value>` (keys: `interval`, `proxy`, `retries`).

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
| **Webhook Alerts** | ✅ Slack/Discord/Feishu/DingTalk | ✅ | ✅ | ✅ |
| **Change History** | ✅ | ✅ | ✅ | ✅ |
| **Open Source** | ✅ | ❌ | ❌ | ❌ |
| **Unlimited URLs** | ✅ | ❌ | ❌ | ❌ |
| **Hosted Dashboard** | [pagewatch.tech](https://pagewatch.tech/?ref=github-compare) | ✅ | ✅ | ✅ |

## Self-Hosting

### pip install

```bash
pip install pagewatch
pagewatch init
pagewatch add https://example.com --name mysite --interval 3600
pagewatch check
```

### Run as a cron job

```bash
# Check every hour
0 * * * * /usr/bin/pagewatch check >> /var/log/pagewatch.log 2>&1
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

## License

MIT — see [LICENSE](LICENSE).

---

<p align="center">
  <b><a href="https://pagewatch.tech">pagewatch.tech</a></b> —
  The free website change detector you can trust.
</p>