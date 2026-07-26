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

All configuration is stored in `~/.pagewatch/`:

```
~/.pagewatch/
  config.json     — global settings (intervals, alerts, proxy)
  watches.json    — list of monitored pages
  snapshots/      — per-page snapshot history (JSON)
```

## Comparison

| Feature | pagewatch | Visualping | ChangeTower | Distill.io |
|---------|-----------|------------|-------------|------------|
| **Price** | **Free & OSS** | $14+/mo | $19+/mo | $15+/mo |
| **Self-Hosted** | ✅ | ❌ | ❌ | ❌ |
| **CSS Selectors** | ✅ | ✅ | ❌ | ✅ |
| **Visual Diff** | via [pagewatch.tech](https://pagewatch.tech/?ref=github-compare) | ✅ | ❌ | ✅ |
| **CLI** | ✅ | ❌ | ❌ | ❌ |
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