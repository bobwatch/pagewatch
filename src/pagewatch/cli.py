#!/usr/bin/env python
import copy
import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .alerts import SUPPORTED_EVENTS, SUPPORTED_FORMATS, AlertManager
from .feed import build_rss
from .importers import IMPORTERS
from .monitor import Monitor
from .service import ServiceError
from .service import install_service as svc_install
from .service import uninstall_service as svc_uninstall
from .storage import Storage, _validate_name
from .utils import default_watch_name, is_valid_url, normalize_url, validate_selector

console = Console()

CONFIG_KEYS = ("interval", "proxy", "retries", "error_threshold", "store_html", "max_history")

_BOOL_TRUE = ("true", "yes", "on", "1")
_BOOL_FALSE = ("false", "no", "off", "0")


def _format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


def get_storage() -> Storage:
    return Storage()


def get_monitor() -> Monitor:
    return Monitor()


def get_alert_manager() -> AlertManager:
    return AlertManager()


def _mask_url(url: str) -> str:
    """Show only scheme://host of a webhook URL — the path often holds a secret token."""
    parsed = urlparse(url or "")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/…"
    return "(hidden)"


def _print_deliveries(deliveries):
    for d in deliveries:
        channel = d.get("channel", "?")
        if d["ok"]:
            console.print(f"[green]Alert sent[/] via '{channel}' ({d['event']})")
        else:
            console.print(f"[red]Alert failed[/] via '{channel}' ({d['event']}): {d['error']}")


def _validate_patterns(patterns):
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            console.print(f"[red]Error: invalid regex '{pattern}': {exc}[/]")
            sys.exit(1)


@click.group()
@click.version_option(version=__version__, prog_name="pagewatch")
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)


@cli.command()
def init():
    storage = get_storage()
    if storage._config_file.is_file():
        console.print("[yellow]Configuration already exists.[/]")
        console.print(f"[dim]Config at: {storage._config_file}[/]")
    else:
        storage.save_config(storage.load_config())
        console.print("[green]Initialized PageWatch configuration.[/]")
        console.print(f"[dim]Config stored at: {storage._config_file}[/]")

    console.print()
    console.print(
        "[bold]Tip:[/] For advanced hosted monitoring with 50+ proxy regions, "
        "email alerts, and visual diffs, visit "
        "[link=https://pagewatch.tech]https://pagewatch.tech[/link]"
    )


@cli.command()
@click.argument("url")
@click.option("--name", "-n", help="Friendly name for this watch")
@click.option("--selector", "-s", help="CSS selector to monitor a specific element")
@click.option("--interval", "-i", type=int, default=None,
              help="Check interval in seconds (default: config 'interval', fallback 3600)")
@click.option("--ignore", "ignore", multiple=True,
              help="Regex: matching text lines are ignored (repeatable). Great for timestamps/counters.")
@click.option("--check-now", is_flag=True, help="Fetch immediately to establish the baseline")
@click.option("--render", is_flag=True, help="Render the page with headless Chromium (requires pagewatch[render])")
@click.option("--alert-filter", default=None,
              help="Regex: only send a change alert when the diff matches (e.g. 'price|stock')")
def add(url, name, selector, interval, ignore, check_now, render, alert_filter):
    url = normalize_url(url)
    if not is_valid_url(url):
        console.print(f"[red]Error: Invalid URL '{url}'[/]")
        sys.exit(1)

    storage = get_storage()

    if interval is None:
        try:
            interval = int(storage.load_config().get("interval"))
        except (TypeError, ValueError):
            interval = 3600
        if interval <= 0:
            interval = 3600
    elif interval <= 0:
        console.print("[red]Error: interval must be a positive number of seconds.[/]")
        sys.exit(1)

    if selector:
        try:
            validate_selector(selector)
        except ValueError as exc:
            console.print(f"[red]Error: {exc}[/]")
            sys.exit(1)

    if not name:
        name = default_watch_name(url)

    _validate_patterns(ignore)
    if alert_filter:
        _validate_patterns([alert_filter])

    if storage.get_watch(name):
        console.print(f"[red]Error: A watch named '{name}' already exists.[/]")
        sys.exit(1)

    try:
        watch = storage.add_watch(name=name, url=url, selector=selector, interval=interval,
                                  ignore_patterns=list(ignore), render=render, alert_filter=alert_filter)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/]")
        sys.exit(1)
    console.print(f"[green]Added watch:[/] {name}")
    console.print(f"  URL:      {url}")
    if selector:
        console.print(f"  Selector: {selector}")
    console.print(f"  Interval: {interval}s")
    if render:
        console.print("  Render:   JS (Playwright)")
    if ignore:
        console.print(f"  Ignoring: {len(ignore)} pattern(s)")
    if alert_filter:
        console.print(f"  Alerts:   only when diff matches /{alert_filter}/")

    if check_now:
        result = get_monitor().check_one(watch)
        if result.get("error"):
            console.print(f"[red]Baseline check failed: {result['error']}[/]")
            sys.exit(1)
        console.print(f"[green]Baseline captured[/] (hash {result['current_hash'][:12]})")


@cli.command()
@click.argument("name")
@click.option("--url", default=None, help="New URL to monitor")
@click.option("--selector", "-s", default=None, help="New CSS selector")
@click.option("--clear-selector", is_flag=True, help="Remove the CSS selector")
@click.option("--interval", "-i", type=int, default=None, help="New check interval in seconds")
@click.option("--add-ignore", multiple=True, help="Add an ignore regex (repeatable)")
@click.option("--remove-ignore", multiple=True, help="Remove an ignore regex (repeatable)")
@click.option("--clear-ignore", is_flag=True, help="Remove all ignore patterns")
@click.option("--pause/--resume", default=None, help="Pause or resume this watch")
@click.option("--render/--no-render", default=None,
              help="Enable/disable JS rendering via headless Chromium (resets the baseline)")
@click.option("--alert-filter", default=None, help="Regex: only alert when the diff matches")
@click.option("--clear-alert-filter", is_flag=True, help="Remove the alert filter (alert on every change)")
def update(name, url, selector, clear_selector, interval, add_ignore, remove_ignore, clear_ignore, pause, render,
           alert_filter, clear_alert_filter):
    """Modify an existing watch (URL, selector, interval, ignore patterns)."""
    storage = get_storage()
    watch = storage.get_watch(name)
    if not watch:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)

    changes = {}
    reset_baseline = False

    if url is not None:
        new_url = normalize_url(url)
        if not is_valid_url(new_url):
            console.print(f"[red]Error: Invalid URL '{new_url}'[/]")
            sys.exit(1)
        changes["url"] = new_url
        reset_baseline = True

    if clear_selector and selector is not None:
        console.print("[red]Error: --selector and --clear-selector cannot be used together.[/]")
        sys.exit(1)

    if clear_selector:
        changes["selector"] = None
        reset_baseline = True
    elif selector is not None:
        try:
            validate_selector(selector)
        except ValueError as exc:
            console.print(f"[red]Error: {exc}[/]")
            sys.exit(1)
        changes["selector"] = selector
        reset_baseline = True

    if interval is not None:
        if interval <= 0:
            console.print("[red]Error: interval must be a positive number of seconds.[/]")
            sys.exit(1)
        changes["interval"] = interval

    if clear_ignore or add_ignore or remove_ignore:
        _validate_patterns(add_ignore)
        old_patterns = list(watch.get("ignore_patterns") or [])
        patterns = [] if clear_ignore else list(old_patterns)
        for pattern in remove_ignore:
            if pattern in patterns:
                patterns.remove(pattern)
            else:
                console.print(f"[yellow]Pattern not present, skipping: {pattern}[/]")
        for pattern in add_ignore:
            if pattern not in patterns:
                patterns.append(pattern)
        changes["ignore_patterns"] = patterns
        if patterns != old_patterns:
            reset_baseline = True

    if pause is not None:
        changes["paused"] = pause

    if render is not None:
        changes["render"] = render
        if bool(render) != bool(watch.get("render")):
            # Rendered and static content differ hugely — keep the old
            # baseline and the next check would fire a false alert.
            reset_baseline = True

    if alert_filter is not None and clear_alert_filter:
        console.print("[red]Error: --alert-filter and --clear-alert-filter cannot be used together.[/]")
        sys.exit(1)
    if clear_alert_filter:
        changes["alert_filter"] = None
    elif alert_filter is not None:
        _validate_patterns([alert_filter])
        changes["alert_filter"] = alert_filter

    if not changes:
        console.print("[yellow]Nothing to update. See 'pagewatch update --help' for options.[/]")
        return

    if reset_baseline:
        changes["last_hash"] = None

    storage.update_watch(name, **changes)
    console.print(f"[green]Updated watch:[/] {name}")
    for key, value in changes.items():
        if key == "last_hash":
            continue
        console.print(f"  {key}: {value}")
    if reset_baseline:
        console.print("[dim]Baseline reset — the next check re-establishes it without alerting.[/]")


@cli.command("list")
@click.option("--search", default=None, help="Filter by name or URL keyword")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--status", type=click.Choice(["active", "paused", "error", "pending"]), default=None, help="Filter by status")
def list_watches(search, tag, status):
    watches = get_storage().load_watches()
    if search:
        search = search.lower()
        watches = [w for w in watches if search in w["name"].lower() or search in w["url"].lower()]
    if tag:
        watches = [w for w in watches if tag in (w.get("tags") or [])]
    if status:
        if status == "paused":
            watches = [w for w in watches if w.get("paused")]
        elif status == "active":
            watches = [w for w in watches if w.get("last_hash") and not w.get("paused")]
        elif status == "error":
            watches = [w for w in watches if w.get("last_status") == "error"]
        elif status == "pending":
            watches = [w for w in watches if not w.get("last_hash") and not w.get("paused")]
    if not watches:
        console.print("[yellow]No watches match the given filters.[/]")
        return

    table = Table(title="Monitored Pages")
    table.add_column("Name", style="cyan")
    table.add_column("URL", style="dim")
    table.add_column("Selector")
    table.add_column("Interval")
    table.add_column("Ignores")
    table.add_column("Checks")
    table.add_column("Errors")
    table.add_column("Last Checked")
    table.add_column("Status")

    for w in watches:
        last = w.get("last_checked") or "Never"
        if w.get("paused"):
            status = "[yellow]paused[/]"
        elif w.get("last_status") == "error":
            status = "[red]error[/]"
        elif w.get("last_hash"):
            status = "[green]active[/]"
        else:
            status = "[yellow]pending[/]"
        if w.get("render"):
            status += " [cyan]JS[/]"
        n_ignores = len(w.get("ignore_patterns") or [])
        table.add_row(
            w["name"],
            w["url"][:50],
            w.get("selector") or "-",
            f"{w.get('interval', '-')}s",
            str(n_ignores) if n_ignores else "-",
            str(w.get("check_count", 0)),
            str(w.get("error_count", 0)),
            last[:19] if last != "Never" else last,
            status,
        )

    console.print(table)


@cli.command()
@click.option("--name", "-n", help="Check a specific watch by name")
@click.option("--no-alerts", is_flag=True, help="Do not dispatch webhook alerts for this run")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON instead of tables")
@click.option("--fail-on-change", is_flag=True, help="Exit with status 2 when any change is detected")
def check(name, no_alerts, as_json, fail_on_change):
    storage = get_storage()
    monitor = get_monitor()

    if name:
        watch = storage.get_watch(name)
        if not watch:
            if as_json:
                click.echo(json.dumps({"error": f"watch '{name}' not found"}))
            else:
                console.print(f"[red]Watch '{name}' not found.[/]")
            sys.exit(1)
        results = [monitor.check_one(watch)]
    else:
        if not as_json:
            console.print("[dim]Checking all watches...[/]")
        results = monitor.check_all()

    if not results:
        if as_json:
            click.echo(json.dumps({"results": [], "alerts": []}))
        else:
            console.print("[yellow]No watches to check.[/]")
        return

    watches = {w["name"]: w for w in storage.load_watches()}
    deliveries = [] if no_alerts else get_alert_manager().dispatch(results, watches=watches)

    if as_json:
        click.echo(json.dumps({"results": results, "alerts": deliveries}, indent=2, ensure_ascii=False))
    else:
        table = Table(title="Check Results")
        table.add_column("Name", style="cyan")
        table.add_column("URL", style="dim")
        table.add_column("Changed", justify="center")
        table.add_column("Hash")
        table.add_column("Error")

        for r in results:
            if r["changed"]:
                changed = "[bold red]YES[/]"
                if r.get("alert_suppressed"):
                    changed += " [dim](alert filtered)[/]"
            else:
                changed = "[green]no[/]"
            h = r.get("current_hash", "-")[:12]
            err = r.get("error") or "-"
            table.add_row(r["name"], r["url"][:40], changed, h, err)

        console.print(table)

        for r in results:
            if r["changed"] and r.get("diff"):
                console.print(f"\n[bold cyan]{r['name']}[/] — Changes detected:")
                console.print(r["diff"][:2000])
                if len(r.get("diff", "")) > 2000:
                    console.print(f"[dim]... diff truncated (use 'pagewatch diff {r['name']}' for full diff)[/]")

        _print_deliveries(deliveries)

    if fail_on_change and any(r.get("changed") for r in results):
        sys.exit(2)


@cli.command()
@click.option("--once", is_flag=True, help="Process currently-due watches once, then exit")
@click.option("--no-alerts", is_flag=True, help="Do not dispatch webhook alerts")
def watch(once, no_alerts):
    """Continuously monitor all watches, honoring each watch's interval."""
    storage = get_storage()
    monitor = get_monitor()
    alert_manager = get_alert_manager()

    if not storage.load_watches():
        console.print("[yellow]No watches configured. Use 'pagewatch add <url>' to add one.[/]")
        return

    def next_due(w, now):
        last = w.get("last_checked")
        interval = int(w.get("interval") or 3600)
        if not last:
            return now
        try:
            ts = datetime.fromisoformat(last).timestamp()
        except ValueError:
            return now
        return max(now, ts + interval)

    now = time.time()
    next_run = {w["name"]: next_due(w, now) for w in storage.load_watches()}
    console.print(f"[bold cyan]PageWatch[/] watching {len(next_run)} page(s). Press Ctrl+C to stop.")

    checked_any = False
    try:
        while True:
            now = time.time()
            # Re-read the watch list every cycle so watches added or removed
            # while the daemon runs are picked up.
            watches = {w["name"]: w for w in storage.load_watches()}
            for name in list(next_run):
                if name not in watches:
                    del next_run[name]
            for name, w in watches.items():
                if name not in next_run:
                    next_run[name] = next_due(w, now)

            for name in [n for n, t in next_run.items() if t <= now]:
                w = watches[name]
                interval = int(w.get("interval") or 3600)
                stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                if w.get("paused"):
                    console.print(f"[dim]{stamp}[/] [yellow]paused[/] {name}")
                    next_run[name] = time.time() + interval
                    continue
                checked_any = True
                try:
                    result = monitor.check_one(w)
                    # Dispatch first so an alert-filtered change is marked on
                    # the result before it is printed.
                    deliveries = [] if no_alerts else alert_manager.dispatch([result], watches=watches)
                    if result.get("error"):
                        console.print(f"[dim]{stamp}[/] [red]error[/] {name}: {result['error']}")
                    elif result["changed"]:
                        suffix = " [dim](alert filtered)[/]" if result.get("alert_suppressed") else ""
                        console.print(f"[dim]{stamp}[/] [bold red]CHANGED[/] {name}{suffix}")
                        if result.get("diff"):
                            console.print(result["diff"][:2000])
                    else:
                        console.print(f"[dim]{stamp}[/] [green]no change[/] {name}")
                    _print_deliveries(deliveries)
                except Exception as exc:  # noqa: BLE001 — a failing check must not kill the daemon
                    console.print(f"[dim]{stamp}[/] [red]error[/] {name}: {exc}")
                next_run[name] = time.time() + interval

            if once:
                if not checked_any:
                    console.print("[dim]No watches due right now.[/]")
                break
            if not next_run:
                time.sleep(1)
                continue
            sleep_for = min(next_run.values()) - time.time()
            time.sleep(min(max(sleep_for, 1), 60))
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/]")


@cli.command()
@click.argument("name")
@click.option("--limit", "-l", type=int, default=20, show_default=True, help="Show the last N entries")
def history(name, limit):
    """Show the snapshot history of a watch."""
    storage = get_storage()
    if not storage.get_watch(name):
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)

    snapshot = storage.load_snapshot(name)
    if not snapshot or not snapshot.get("history"):
        console.print(f"[yellow]No snapshot history for '{name}'. Run 'pagewatch check' first.[/]")
        return

    entries = snapshot["history"]
    shown = entries[-max(1, limit):]
    latest_hash = snapshot.get("latest", {}).get("content_hash")
    prev_hash = snapshot.get("previous", {}).get("content_hash")

    table = Table(title=f"History — {name} (showing {len(shown)} of {len(entries)})")
    table.add_column("#", justify="right")
    table.add_column("Timestamp")
    table.add_column("Hash")
    table.add_column("Text Length", justify="right")
    table.add_column("Note")

    start = len(entries) - len(shown)
    for i, entry in enumerate(shown, start=start + 1):
        h = entry.get("content_hash", "")
        if h and h == latest_hash:
            note = "current"
        elif h and h == prev_hash:
            note = "previous"
        else:
            note = ""
        table.add_row(
            str(i),
            str(entry.get("timestamp", ""))[:19],
            h[:12],
            str(entry.get("text_length", "")),
            note,
        )

    console.print(table)


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json",
              show_default=True, help="Export format")
@click.option("--output", "-o", type=click.Path(dir_okay=False), default=None,
              help="Write to a file instead of stdout")
@click.option("--include-html", is_flag=True, help="Include raw HTML snapshots in JSON export")
def export(fmt, output, include_html):
    """Export watches and snapshot history as JSON or CSV."""
    storage = get_storage()
    watches = storage.load_watches()

    if include_html and not storage._store_html:
        click.echo("Warning: HTML storage is disabled (store_html=false) — "
                   "exported snapshots contain no HTML.", err=True)

    if fmt == "json":
        snapshots = {}
        for w in watches:
            snap = storage.load_snapshot(w["name"])
            if not snap:
                continue
            latest = dict(snap.get("latest", {}))
            if not include_html:
                latest.pop("html", None)
            entry = {"history": snap.get("history", []), "latest": latest}
            if snap.get("previous"):
                entry["previous"] = snap["previous"]
            snapshots[w["name"]] = entry
        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "pagewatch_version": __version__,
            "watches": watches,
            "snapshots": snapshots,
        }
        text = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "url", "timestamp", "content_hash", "text_length"])
        for w in watches:
            snap = storage.load_snapshot(w["name"]) or {}
            for entry in snap.get("history", []):
                writer.writerow([
                    w["name"], w["url"],
                    entry.get("timestamp"), entry.get("content_hash"), entry.get("text_length"),
                ])
        text = buf.getvalue()

    if output:
        if any(w.get("headers") for w in watches):
            click.echo("Warning: backup contains per-watch headers which may include credentials.", err=True)
        try:
            Path(output).write_text(text, encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]Cannot write export to '{output}': {exc}[/]")
            sys.exit(1)
        console.print(f"[green]Exported to {output}[/]")
    else:
        click.echo(text, nl=False)
        if not text.endswith("\n"):
            click.echo()


@cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--replace", is_flag=True, help="Replace the existing watch list instead of merging")
@click.option("--from", "source", type=click.Choice(list(IMPORTERS)), default=None,
              help="Import a competitor export (changedetection.io / Distill.io) instead of a pagewatch backup")
def import_cmd(file, replace, source):
    """Import watches (and snapshots) from a 'pagewatch export' JSON file.

    With --from, import a changedetection.io or Distill.io export instead."""
    storage = get_storage()
    try:
        text = Path(file).read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Cannot read backup: {exc}[/]")
        sys.exit(1)

    if source:
        try:
            watches, warnings = IMPORTERS[source](text)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            sys.exit(1)
        for warning in warnings:
            console.print(f"[yellow]{warning}[/]")
        snapshots = {}
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Cannot read backup: {exc}[/]")
            sys.exit(1)
        watches = data.get("watches") if isinstance(data, dict) else None
        if not isinstance(watches, list):
            console.print("[red]Invalid backup: missing 'watches' list.[/]")
            sys.exit(1)
        snapshots = data.get("snapshots") or {}

    valid, invalid = [], 0
    for w in watches:
        if not isinstance(w, dict):
            invalid += 1
            console.print("[yellow]Skipping entry: not an object.[/]")
            continue
        name = w.get("name")
        problem = None
        try:
            _validate_name(str(name or ""))
        except ValueError as exc:
            problem = f"invalid name ({exc})"
        if problem is None and not (isinstance(w.get("url"), str) and is_valid_url(w["url"])):
            problem = f"invalid URL: {w.get('url')!r}"
        if problem is None:
            try:
                interval = int(w.get("interval", 3600))
                if interval <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                problem = f"invalid interval: {w.get('interval')!r}"
        if problem is None and "tags" in w and not isinstance(w["tags"], list):
            problem = "tags must be a list"
        if problem is None and "headers" in w and not isinstance(w["headers"], dict):
            problem = "headers must be a dict"
        if problem:
            invalid += 1
            console.print(f"[yellow]Skipping watch '{name or '?'}': {problem}.[/]")
            continue
        valid.append(w)

    if replace:
        for w in storage.load_watches():
            storage.remove_watch(w["name"])
        storage.save_watches(valid)
        imported = [w["name"] for w in valid]
        skipped = []
    else:
        current = storage.load_watches()
        existing_names = {w["name"] for w in current}
        imported, skipped = [], []
        for w in valid:
            if w["name"] in existing_names:
                skipped.append(w["name"])
                continue
            current.append(w)
            imported.append(w["name"])
        storage.save_watches(current)

    restored = 0
    for wname in imported:
        snap = snapshots.get(wname)
        if isinstance(snap, dict) and snap.get("history"):
            try:
                storage.restore_snapshot(wname, snap)
                restored += 1
            except ValueError as exc:
                console.print(f"[yellow]Skipping snapshot for '{wname}': {exc}[/]")

    message = f"[green]Imported {len(imported)} watch(es)[/]"
    if skipped:
        message += f", skipped {len(skipped)} existing"
    if invalid:
        message += f", skipped {invalid} invalid"
    console.print(message)
    if restored:
        console.print(f"[dim]Restored snapshot history for {restored} watch(es).[/]")


@cli.group()
def alert():
    """Manage webhook alert channels (generic, Slack, Discord, Feishu, DingTalk)."""


@alert.command("add")
@click.argument("url")
@click.option("--name", "-n", default=None, help="Channel name (default: webhook-N)")
@click.option("--format", "fmt", type=click.Choice(list(SUPPORTED_FORMATS)), default="generic",
              show_default=True, help="Webhook payload format (telegram URLs must carry ?chat_id=...)")
@click.option("--events", type=click.Choice(list(SUPPORTED_EVENTS)), default="change",
              show_default=True, help="Which events trigger this channel")
def alert_add(url, name, fmt, events):
    """Register a webhook URL to receive change/error alerts."""
    try:
        channel = get_alert_manager().add_channel(url, name=name, fmt=fmt, events=events)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/]")
        sys.exit(1)
    console.print(f"[green]Added alert channel:[/] {channel['name']}")
    console.print(f"  URL:    {_mask_url(channel['url'])}")
    console.print(f"  Format: {channel['format']}")
    console.print(f"  Events: {channel['events']}")


@alert.command("list")
def alert_list():
    """List configured alert channels."""
    channels = get_alert_manager().list_channels()
    if not channels:
        console.print("[yellow]No alert channels configured. Use 'pagewatch alert add <url>' to add one.[/]")
        return
    table = Table(title="Alert Channels")
    table.add_column("Name", style="cyan")
    table.add_column("Format")
    table.add_column("Events")
    table.add_column("URL", style="dim")
    for c in channels:
        table.add_row(c.get("name"), c.get("format", "generic"), c.get("events", "change"), _mask_url(c.get("url", "")))
    console.print(table)


@alert.command("remove")
@click.argument("name")
def alert_remove(name):
    """Remove an alert channel by name."""
    if get_alert_manager().remove_channel(name):
        console.print(f"[green]Removed alert channel: {name}[/]")
    else:
        console.print(f"[red]Alert channel '{name}' not found.[/]")
        sys.exit(1)


@alert.command("update")
@click.argument("name")
@click.option("--url", default=None, help="New webhook URL")
@click.option("--format", "fmt", type=click.Choice(list(SUPPORTED_FORMATS)), default=None, help="New payload format")
@click.option("--events", type=click.Choice(list(SUPPORTED_EVENTS)), default=None, help="Which events to subscribe to")
def alert_update(name, url, fmt, events):
    """Update an existing alert channel."""
    kwargs = {}
    if url is not None:
        if not url.lower().startswith(("http://", "https://")):
            console.print("[red]Error: Webhook URL must start with http:// or https://[/]")
            sys.exit(1)
        kwargs["url"] = url
    if fmt is not None:
        kwargs["format"] = fmt
    if events is not None:
        kwargs["events"] = events
    if not kwargs:
        console.print("[yellow]Nothing to update. Provide --url, --format, or --events.[/]")
        return
    channel = get_alert_manager().update_channel(name, **kwargs)
    if not channel:
        console.print(f"[red]Alert channel '{name}' not found.[/]")
        sys.exit(1)
    console.print(f"[green]Updated alert channel:[/] {name}")
    for key, value in channel.items():
        if key == "name":
            continue
        console.print(f"  {key}: {value}")


@alert.command("test")
@click.option("--name", "-n", default=None, help="Test a single channel by name (default: all)")
def alert_test(name):
    """Send a test alert to all configured channels (webhooks + email)."""
    manager = get_alert_manager()
    no_channels = not manager.list_channels() and not manager.get_email_config().get("smtp_host")
    if name is None and no_channels:
        console.print("[yellow]No alert channels configured. Use 'pagewatch alert add <url>' or 'pagewatch alert email set' first.[/]")
        return
    try:
        deliveries = manager.send_test(name)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/]")
        sys.exit(1)
    if not deliveries:
        console.print("[yellow]No alert channels configured. Use 'pagewatch alert add <url>' or 'pagewatch alert email set' first.[/]")
        return
    _print_deliveries(deliveries)
    webhook_ok = any(d.get("ok") and d.get("channel") != "email" for d in deliveries)
    email_ok = any(d.get("channel") == "email" and d.get("ok") for d in deliveries)
    if not webhook_ok and not email_ok:
        sys.exit(1)


@alert.group()
def email():
    """Configure email alert settings (SMTP)."""


@email.command("set")
@click.option("--smtp-host", required=True, help="SMTP server hostname")
@click.option("--smtp-port", type=int, default=587, show_default=True, help="SMTP server port")
@click.option("--smtp-user", default=None, help="SMTP username (optional)")
@click.option("--smtp-pass", default=None, help="SMTP password (optional)")
@click.option("--no-tls", is_flag=True, help="Disable TLS (use plain SMTP)")
@click.option("--from-addr", default=None, help="From address (defaults to SMTP user)")
@click.option("--to-addrs", required=True, help="Comma-separated recipient addresses")
def email_set(smtp_host, smtp_port, smtp_user, smtp_pass, no_tls, from_addr, to_addrs):
    """Configure SMTP email alerts."""
    try:
        cfg = get_alert_manager().set_email_config(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_pass=smtp_pass,
            smtp_tls=not no_tls,
            from_addr=from_addr,
            to_addrs=to_addrs,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/]")
        sys.exit(1)
    console.print("[green]Email alert settings saved.[/]")
    console.print(f"  SMTP:   {cfg['smtp_host']}:{cfg['smtp_port']}")
    console.print(f"  TLS:    {cfg['smtp_tls']}")
    console.print(f"  From:   {cfg.get('from_addr', '-')}")
    console.print(f"  To:     {cfg.get('to_addrs', '-')}")


@email.command("show")
def email_show():
    """Show current email alert configuration."""
    cfg = get_alert_manager().get_email_config()
    if not cfg.get("smtp_host"):
        console.print("[yellow]Email alerts not configured. Use 'pagewatch alert email set' to configure.[/]")
        return
    console.print("[bold]Email Alert Configuration[/]")
    console.print(f"  SMTP host: {cfg.get('smtp_host', '-')}")
    console.print(f"  SMTP port: {cfg.get('smtp_port', '-')}")
    console.print(f"  SMTP user: {cfg.get('smtp_user', '-')}")
    console.print(f"  SMTP pass: {'***' if cfg.get('smtp_pass') else '(not set)'}")
    console.print(f"  TLS:       {cfg.get('smtp_tls', True)}")
    console.print(f"  From:      {cfg.get('from_addr', '-')}")
    console.print(f"  To:        {cfg.get('to_addrs', '-')}")


@cli.command()
@click.argument("name")
def diff(name):
    storage = get_storage()
    watch = storage.get_watch(name)
    if not watch:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)

    snapshot = storage.load_snapshot(name)
    if not snapshot or not snapshot.get("history"):
        console.print(f"[yellow]No snapshot history for '{name}'. Run 'pagewatch check' first.[/]")
        return

    entries = snapshot["history"]
    console.print(f"[dim]Snapshot history: {len(entries)} entries[/]")

    diff_output = get_monitor().diff(name)
    if diff_output is None:
        latest = entries[-1]
        console.print(f"[yellow]Only one distinct snapshot so far (from {latest['timestamp']}). No diff possible yet.[/]")
        return

    console.print(f"\n[bold]Diff for {name}[/]")
    console.print(f"[dim]  Latest:   {snapshot.get('latest', {}).get('updated_at', '-')}[/]")
    console.print(f"[dim]  Previous: {snapshot.get('previous', {}).get('updated_at', '-')}[/]")
    console.print()

    if not diff_output:
        console.print("[yellow]No differences found.[/]")
        return

    for line in diff_output.split("\n"):
        if line.startswith(("---", "+++")):
            console.print(f"[bold]{line}[/]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/]")
        else:
            console.print(f"[dim]{line}[/]")


@cli.command()
@click.argument("name", required=False)
@click.option("--limit", "-l", type=int, default=50, show_default=True, help="Max number of feed items")
def feed(name, limit):
    """Print an RSS 2.0 feed of detected changes (all watches, or just NAME)."""
    storage = get_storage()
    watches = storage.load_watches()
    if name is not None:
        watch = storage.get_watch(name)
        if not watch:
            console.print(f"[red]Watch '{name}' not found.[/]")
            sys.exit(1)
        watches = [watch]

    watches_entries = []
    for w in watches:
        snapshot = storage.load_snapshot(w["name"]) or {}
        watches_entries.append({
            "name": w["name"],
            "url": w.get("url"),
            "history": snapshot.get("history", []),
        })
    title = f"pagewatch changes — {name}" if name else "pagewatch changes"
    link = watches[0].get("url") if name and watches else ""
    click.echo(build_rss(watches_entries, title, link,
                         "Detected changes from pagewatch watches.", limit=limit), nl=False)


@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx):
    """Show configuration, or change it with 'pagewatch config set'."""
    if ctx.invoked_subcommand is not None:
        return
    storage = get_storage()
    cfg = copy.deepcopy(storage.load_config())
    email_cfg = cfg.get("alerts", {}).get("email")
    if isinstance(email_cfg, dict) and "smtp_pass_obfuscated" in email_cfg:
        email_cfg["smtp_pass_obfuscated"] = "***"
    console.print("[bold]PageWatch Configuration[/]")
    console.print(f"  Config file: {storage._config_file}")
    console.print(f"  Watch file:  {storage._watches_file}")
    console.print(f"  Snapshots:   {storage._snapshots_dir}")
    console.print()
    console.print_json(data=cfg)


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """Set a config value. Keys: interval, proxy, retries, error_threshold,
    store_html, max_history ('none' clears proxy)."""
    storage = get_storage()
    cfg = storage.load_config()

    if key == "interval":
        try:
            parsed = int(value)
        except ValueError:
            parsed = 0
        if parsed <= 0:
            console.print("[red]Error: interval must be a positive integer (seconds).[/]")
            sys.exit(1)
        cfg["interval"] = parsed
    elif key == "retries":
        try:
            parsed = int(value)
        except ValueError:
            parsed = -1
        if not 0 <= parsed <= 10:
            console.print("[red]Error: retries must be an integer between 0 and 10.[/]")
            sys.exit(1)
        cfg["retries"] = parsed
    elif key == "error_threshold":
        try:
            parsed = int(value)
        except ValueError:
            parsed = 0
        if parsed <= 0:
            console.print("[red]Error: error_threshold must be a positive integer "
                          "(alert after N consecutive failures).[/]")
            sys.exit(1)
        cfg["error_threshold"] = parsed
    elif key == "proxy":
        value = value.strip()
        if value.lower() in ("none", "null", ""):
            cfg["proxy"] = None
        elif re.match(r"^(https?|socks5)://", value):
            cfg["proxy"] = value
        else:
            console.print("[red]Error: proxy must start with http://, https://, or socks5:// (or 'none' to clear).[/]")
            sys.exit(1)
    elif key == "store_html":
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE:
            cfg["store_html"] = True
        elif lowered in _BOOL_FALSE:
            cfg["store_html"] = False
        else:
            console.print("[red]Error: store_html must be true/false (yes/no/on/off also accepted).[/]")
            sys.exit(1)
    elif key == "max_history":
        try:
            parsed = int(value)
        except ValueError:
            parsed = 0
        if parsed <= 0:
            console.print("[red]Error: max_history must be a positive integer.[/]")
            sys.exit(1)
        cfg["max_history"] = parsed
    else:
        console.print(f"[red]Error: unknown key '{key}'. Valid keys: {', '.join(CONFIG_KEYS)}[/]")
        sys.exit(1)

    storage.save_config(cfg)
    console.print(f"[green]Set {key} = {cfg[key]}[/]")


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to bind")
@click.option("--port", "-p", type=int, default=8787, show_default=True, help="Port to listen on")
@click.option("--no-browser", is_flag=True, help="Do not open the dashboard in a browser")
@click.option(
    "--token",
    default=None,
    envvar="PAGEWATCH_TOKEN",
    help="Require this bearer token for API access (env: PAGEWATCH_TOKEN)",
)
def serve(host, port, no_browser, token):
    """Start the local web dashboard (REST API + UI)."""
    import threading
    import webbrowser

    from .server import PagewatchServer

    try:
        server = PagewatchServer((host, port), token=token)
    except OSError as exc:
        console.print(f"[red]Cannot bind {host}:{port} — {exc}[/]")
        sys.exit(1)

    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{port}"
    ui_built = (server.webui_dir / "index.html").is_file()

    console.print(f"[bold cyan]PageWatch[/] dashboard: [link={url}]{url}[/link]")
    console.print(f"[dim]Data dir: {server.storage._root}[/]")
    if not ui_built:
        console.print("[yellow]Web UI assets not built — serving the JSON API with a placeholder page.[/]")
        console.print("[dim]Build them with: cd apps/web && npm install && npm run build[/]")
    if host not in ("127.0.0.1", "localhost") and not token:
        console.print("[yellow]Warning: no authentication — do not expose this server to untrusted networks.[/]")
        console.print("[dim]Tip: use --token (or PAGEWATCH_TOKEN) to require API authentication.[/]")
    elif token:
        console.print("[dim]API authentication enabled (the dashboard will prompt for the token).[/]")
    console.print("[dim]Press Ctrl+C to stop.[/]")

    if not no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")
    finally:
        server.server_close()


@cli.command()
@click.argument("name")
def remove(name):
    if get_storage().remove_watch(name):
        console.print(f"[green]Removed watch: {name}[/]")
    else:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)


@cli.command()
@click.argument("name")
@click.option("--new-name", default=None, help="Name for the cloned watch (default: {name}-copy)")
def clone(name, new_name):
    """Clone an existing watch with all its configuration."""
    storage = get_storage()
    watch = storage.get_watch(name)
    if not watch:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)
    new_name = new_name or f"{name}-copy"
    if storage.get_watch(new_name):
        console.print(f"[red]A watch named '{new_name}' already exists.[/]")
        sys.exit(1)
    cloned = storage.add_watch(
        name=new_name, url=watch["url"], selector=watch.get("selector"),
        interval=watch.get("interval", 3600),
        ignore_patterns=list(watch.get("ignore_patterns") or []),
        paused=watch.get("paused", False),
        tags=list(watch.get("tags") or []),
        headers=dict(watch.get("headers") or {}),
        render=watch.get("render", False),
    )
    console.print(f"[green]Cloned watch:[/] {name} → {new_name}")
    console.print(f"  URL:      {cloned['url']}")


@cli.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--delimiter", default=",", help="CSV delimiter (default: comma)")
def import_csv(file, delimiter):
    """Import watches from a CSV file (name, url, selector, interval, tags)."""
    storage = get_storage()
    imported = 0
    skipped = 0
    try:
        with open(file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for lineno, row in enumerate(reader, start=2):
                name = (row.get("name") or "").strip()
                url = (row.get("url") or "").strip()
                if not name or not url:
                    skipped += 1
                    console.print(f"[yellow]Skipping row {lineno}: missing name or url.[/]")
                    continue
                if storage.get_watch(name):
                    skipped += 1
                    continue
                url = normalize_url(url)
                if not is_valid_url(url):
                    skipped += 1
                    console.print(f"[yellow]Skipping row {lineno}: invalid URL '{url}'.[/]")
                    continue
                try:
                    interval = int(row.get("interval") or 3600)
                    if interval <= 0:
                        raise ValueError("interval must be a positive number of seconds")
                    selector = (row.get("selector") or "").strip() or None
                    if selector:
                        validate_selector(selector)
                    tags = [t.strip() for t in (row.get("tags") or "").split(";") if t.strip()]
                    storage.add_watch(name=name, url=url, selector=selector, interval=interval, tags=tags)
                except (ValueError, TypeError) as exc:
                    skipped += 1
                    console.print(f"[yellow]Skipping row {lineno}: {exc}[/]")
                    continue
                imported += 1
    except (OSError, csv.Error) as exc:
        console.print(f"[red]CSV import failed: {exc}[/]")
        sys.exit(1)
    console.print(f"[green]Imported {imported} watch(es) from CSV.[/]")
    if skipped:
        console.print(f"[yellow]Skipped {skipped} invalid or duplicate entries.[/]")


@cli.command()
@click.argument("name")
def pause(name):
    """Pause a watch — it will be skipped during checks."""
    storage = get_storage()
    if not storage.get_watch(name):
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)
    storage.update_watch(name, paused=True)
    console.print(f"[yellow]Paused watch:[/] {name}")


@cli.command()
@click.argument("name")
def resume(name):
    """Resume a paused watch."""
    storage = get_storage()
    if not storage.get_watch(name):
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)
    storage.update_watch(name, paused=False)
    console.print(f"[green]Resumed watch:[/] {name}")


@cli.command()
def stats():
    """Show monitoring statistics and disk usage."""
    storage = get_storage()
    s = storage.get_stats()
    usage = storage.get_disk_usage()

    console.print("[bold]PageWatch Statistics[/]")
    console.print(f"  Watches:   {s['total_watches']} total, {s['active_watches']} active, "
                  f"{s['paused_watches']} paused, {s['errored_watches']} errored")
    console.print(f"  Checks:    {s['total_checks']} total, {s['total_errors']} errors "
                  f"({s['error_rate']}% error rate)")
    console.print(f"  Changes:   {s['changes_today']} today, {s['changes_week']} this week, "
                  f"{s['changes_month']} this month")

    if s["top_changed"]:
        table = Table(title="Most Active Watches")
        table.add_column("#", justify="right")
        table.add_column("Name", style="cyan")
        table.add_column("Snapshots", justify="right")
        for i, w in enumerate(s["top_changed"], start=1):
            table.add_row(str(i), w["name"], str(w["snapshots"]))
        console.print(table)

    console.print(f"  Disk usage: {_format_bytes(usage['total_bytes'])} total "
                  f"({_format_bytes(usage['snapshots_bytes'])} in snapshots)")
    top_usage = usage["per_watch"][:5]
    if top_usage:
        table = Table(title="Largest Snapshot Files")
        table.add_column("#", justify="right")
        table.add_column("Name", style="cyan")
        table.add_column("Size", justify="right")
        for i, w in enumerate(top_usage, start=1):
            table.add_row(str(i), w["name"], _format_bytes(w["bytes"]))
        console.print(table)


@cli.command("install-service")
def install_service_cmd():
    """Install pagewatch as a user service (systemd on Linux, launchd on macOS)."""
    try:
        target = svc_install()
    except ServiceError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)
    console.print(f"[green]Service installed and started:[/] {target}")
    console.print("[dim]Check status with: systemctl --user status pagewatch (Linux) "
                  "or launchctl list | grep pagewatch (macOS)[/]")


@cli.command("uninstall-service")
def uninstall_service_cmd():
    """Stop and remove the pagewatch user service."""
    try:
        target = svc_uninstall()
    except ServiceError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)
    console.print(f"[green]Service removed:[/] {target}")


if __name__ == "__main__":
    cli()
