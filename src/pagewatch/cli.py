#!/usr/bin/env python
import csv
import io
import json
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
        if d["ok"]:
            console.print(f"[green]Alert sent[/] via '{d['channel']}' ({d['event']})")
        else:
            console.print(f"[red]Alert failed[/] via '{d['channel']}' ({d['event']}): {d['error']}")


@click.group()
@click.version_option(version=__version__, prog_name="pagewatch")
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)


@cli.command()
def init():
    storage = get_storage()
    config = storage.load_config()
    if not config or config == {"interval": 3600, "alerts": {}, "proxy": None}:
        config = {
            "interval": 3600,
            "alerts": {},
            "proxy": None,
        }
        storage.save_config(config)
        console.print("[green]Initialized PageWatch configuration.[/]")
        console.print(f"[dim]Config stored at: {storage._config_file}[/]")
    else:
        console.print("[yellow]Configuration already exists.[/]")
        console.print(f"[dim]Config at: {storage._config_file}[/]")

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
def add(url, name, selector, interval):
    url = normalize_url(url)
    if not is_valid_url(url):
        console.print(f"[red]Error: Invalid URL '{url}'[/]")
        sys.exit(1)

    if not name:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        name = parsed.netloc.replace(".", "-")

    storage = get_storage()
    if storage.get_watch(name):
        console.print(f"[red]Error: A watch named '{name}' already exists.[/]")
        sys.exit(1)

    storage.add_watch(name=name, url=url, selector=selector, interval=interval)
    console.print(f"[green]Added watch:[/] {name}")
    console.print(f"  URL:      {url}")
    if selector:
        console.print(f"  Selector: {selector}")
    console.print(f"  Interval: {interval}s")


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
    table.add_column("Last Checked")
    table.add_column("Status")

    for w in watches:
        last = w.get("last_checked") or "Never"
        status = "[green]active[/]" if w.get("last_hash") else "[yellow]pending[/]"
        table.add_row(
            w["name"],
            w["url"][:50],
            w.get("selector") or "-",
            f"{w.get('interval', '-')}s",
            last[:19] if last != "Never" else last,
            status,
        )

    console.print(table)


@cli.command()
@click.option("--name", "-n", help="Check a specific watch by name")
@click.option("--no-alerts", is_flag=True, help="Do not dispatch webhook alerts for this run")
def check(name, no_alerts):
    storage = get_storage()
    monitor = get_monitor()

    if name:
        watch = storage.get_watch(name)
        if not watch:
            console.print(f"[red]Watch '{name}' not found.[/]")
            sys.exit(1)
        results = [monitor.check_one(watch)]
    else:
        console.print("[dim]Checking all watches...[/]")
        results = monitor.check_all()

    if not results:
        console.print("[yellow]No watches to check.[/]")
        return

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

    if not no_alerts:
        _print_deliveries(get_alert_manager().dispatch(results))


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
    """Send a test alert to configured channels."""
    manager = get_alert_manager()
    try:
        deliveries = manager.send_test(name)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/]")
        sys.exit(1)
    if not deliveries:
        console.print("[yellow]No alert channels configured. Use 'pagewatch alert add <url>' to add one.[/]")
        return
    _print_deliveries(deliveries)
    if any(not d["ok"] for d in deliveries):
        sys.exit(1)


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

    history = snapshot["history"]
    console.print(f"[dim]Snapshot history: {len(history)} entries[/]")

    diff_output = get_monitor().diff(name)
    if diff_output is None:
        latest = history[-1]
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


@cli.command()
def config():
    storage = get_storage()
    cfg = storage.load_config()
    console.print("[bold]PageWatch Configuration[/]")
    console.print(f"  Config file: {storage._config_file}")
    console.print(f"  Watch file:  {storage._watches_file}")
    console.print(f"  Snapshots:   {storage._snapshots_dir}")
    console.print()
    console.print_json(data=cfg)


@cli.command()
@click.argument("name")
def remove(name):
    if get_storage().remove_watch(name):
        console.print(f"[green]Removed watch: {name}[/]")
    else:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)


if __name__ == "__main__":
    print_banner()
    cli()
