#!/usr/bin/env python
import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .alerts import SUPPORTED_EVENTS, SUPPORTED_FORMATS, AlertManager
from .monitor import Monitor
from .storage import Storage
from .utils import is_valid_url, normalize_url

console = Console()

CONFIG_KEYS = ("interval", "proxy", "retries")


def get_storage() -> Storage:
    return Storage()


def get_monitor() -> Monitor:
    return Monitor()


def get_alert_manager() -> AlertManager:
    return AlertManager()


def print_banner():
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]PageWatch[/] — Free & Open Source Website Change Monitor",
            border_style="cyan",
        )
    )
    console.print("[dim]For hosted monitoring with 50+ proxy regions, visual diffs, and team collaboration,[/]")
    console.print("[dim]visit [link=https://pagewatch.tech]https://pagewatch.tech[/link][/]\n")


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
@click.option("--interval", "-i", type=int, default=3600, help="Check interval in seconds (default: 3600)")
@click.option("--ignore", "ignore", multiple=True,
              help="Regex: matching text lines are ignored (repeatable). Great for timestamps/counters.")
@click.option("--check-now", is_flag=True, help="Fetch immediately to establish the baseline")
def add(url, name, selector, interval, ignore, check_now):
    url = normalize_url(url)
    if not is_valid_url(url):
        console.print(f"[red]Error: Invalid URL '{url}'[/]")
        sys.exit(1)

    if not name:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        name = parsed.netloc.replace(".", "-")

    _validate_patterns(ignore)

    storage = get_storage()
    if storage.get_watch(name):
        console.print(f"[red]Error: A watch named '{name}' already exists.[/]")
        sys.exit(1)

    watch = storage.add_watch(name=name, url=url, selector=selector, interval=interval,
                              ignore_patterns=list(ignore))
    console.print(f"[green]Added watch:[/] {name}")
    console.print(f"  URL:      {url}")
    if selector:
        console.print(f"  Selector: {selector}")
    console.print(f"  Interval: {interval}s")
    if ignore:
        console.print(f"  Ignoring: {len(ignore)} pattern(s)")

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
def update(name, url, selector, clear_selector, interval, add_ignore, remove_ignore, clear_ignore, pause):
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

    if clear_selector:
        changes["selector"] = None
        reset_baseline = True
    elif selector is not None:
        changes["selector"] = selector
        reset_baseline = True

    if interval is not None:
        if interval <= 0:
            console.print("[red]Error: interval must be a positive number of seconds.[/]")
            sys.exit(1)
        changes["interval"] = interval

    if clear_ignore or add_ignore or remove_ignore:
        _validate_patterns(add_ignore)
        patterns = [] if clear_ignore else list(watch.get("ignore_patterns") or [])
        for pattern in remove_ignore:
            if pattern in patterns:
                patterns.remove(pattern)
            else:
                console.print(f"[yellow]Pattern not present, skipping: {pattern}[/]")
        for pattern in add_ignore:
            if pattern not in patterns:
                patterns.append(pattern)
        changes["ignore_patterns"] = patterns
        reset_baseline = True

    if not changes:
        if pause is not None:
            changes["paused"] = pause
            storage.update_watch(name, **changes)
            state = "paused" if pause else "resumed"
            console.print(f"[green]{state.capitalize()} watch:[/] {name}")
            return
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
def list_watches():
    watches = get_storage().load_watches()
    if not watches:
        console.print("[yellow]No watches configured. Use 'pagewatch add <url>' to add one.[/]")
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
        elif w.get("last_hash"):
            status = "[green]active[/]"
        else:
            status = "[yellow]pending[/]"
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

    deliveries = [] if no_alerts else get_alert_manager().dispatch(results)

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
            changed = "[bold red]YES[/]" if r["changed"] else "[green]no[/]"
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

    watches = storage.load_watches()
    if not watches:
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
    next_run = {w["name"]: next_due(w, now) for w in watches}
    console.print(f"[bold cyan]PageWatch[/] watching {len(watches)} page(s). Press Ctrl+C to stop.")

    checked_any = False
    try:
        while True:
            now = time.time()
            for name in [n for n, t in next_run.items() if t <= now]:
                checked_any = True
                w = storage.get_watch(name)
                if w is None:
                    next_run.pop(name, None)
                    continue
                result = monitor.check_one(w)
                stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                if result.get("error"):
                    console.print(f"[dim]{stamp}[/] [red]error[/] {name}: {result['error']}")
                elif result["changed"]:
                    console.print(f"[dim]{stamp}[/] [bold red]CHANGED[/] {name}")
                    if result.get("diff"):
                        console.print(result["diff"][:2000])
                else:
                    console.print(f"[dim]{stamp}[/] [green]no change[/] {name}")
                if not no_alerts:
                    _print_deliveries(alert_manager.dispatch([result]))
                next_run[name] = time.time() + int(w.get("interval") or 3600)

            if once:
                if not checked_any:
                    console.print("[dim]No watches due right now.[/]")
                break
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
        Path(output).write_text(text, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/]")
    else:
        click.echo(text, nl=False)
        if not text.endswith("\n"):
            click.echo()


@cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--replace", is_flag=True, help="Replace the existing watch list instead of merging")
def import_cmd(file, replace):
    """Import watches and snapshots from a 'pagewatch export' JSON file."""
    storage = get_storage()
    try:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Cannot read backup: {exc}[/]")
        sys.exit(1)

    watches = data.get("watches") if isinstance(data, dict) else None
    if not isinstance(watches, list):
        console.print("[red]Invalid backup: missing 'watches' list.[/]")
        sys.exit(1)

    incoming = [w for w in watches if isinstance(w, dict) and w.get("name") and w.get("url")]
    snapshots = data.get("snapshots") or {}

    if replace:
        for w in storage.load_watches():
            storage.remove_watch(w["name"])
        storage.save_watches(incoming)
        imported = [w["name"] for w in incoming]
        skipped = []
    else:
        current = storage.load_watches()
        existing_names = {w["name"] for w in current}
        imported, skipped = [], []
        for w in incoming:
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
            storage.restore_snapshot(wname, snap)
            restored += 1

    message = f"[green]Imported {len(imported)} watch(es)[/]"
    if skipped:
        message += f", skipped {len(skipped)} existing"
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
              show_default=True, help="Webhook payload format")
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
    console.print(f"  URL:    {channel['url']}")
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
        table.add_row(c.get("name"), c.get("format", "generic"), c.get("events", "change"), c.get("url", "")[:60])
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


@alert.command("test")
@click.option("--name", "-n", default=None, help="Test a single channel by name (default: all)")
def alert_test(name):
    """Send a test alert to all configured channels (webhooks + email)."""
    manager = get_alert_manager()
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
        if line.startswith("---") or line.startswith("+++"):
            console.print(f"[bold]{line}[/]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/]")
        else:
            console.print(f"[dim]{line}[/]")


@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx):
    """Show configuration, or change it with 'pagewatch config set'."""
    if ctx.invoked_subcommand is not None:
        return
    storage = get_storage()
    cfg = storage.load_config()
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
    """Set a config value. Keys: interval, proxy, retries ('none' clears proxy)."""
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
    elif key == "proxy":
        cfg["proxy"] = None if value.strip().lower() in ("none", "null", "") else value.strip()
    else:
        console.print(f"[red]Error: unknown key '{key}'. Valid keys: {', '.join(CONFIG_KEYS)}[/]")
        sys.exit(1)

    storage.save_config(cfg)
    console.print(f"[green]Set {key} = {cfg[key]}[/]")


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to bind")
@click.option("--port", "-p", type=int, default=8787, show_default=True, help="Port to listen on")
@click.option("--no-browser", is_flag=True, help="Do not open the dashboard in a browser")
def serve(host, port, no_browser):
    """Start the local web dashboard (REST API + UI)."""
    import threading
    import webbrowser

    from .server import PagewatchServer

    try:
        server = PagewatchServer((host, port))
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
    if host not in ("127.0.0.1", "localhost"):
        console.print("[yellow]Warning: no authentication — do not expose this server to untrusted networks.[/]")
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


if __name__ == "__main__":
    print_banner()
    cli()
