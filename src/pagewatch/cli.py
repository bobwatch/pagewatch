#!/usr/bin/env python
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .monitor import Monitor
from .storage import Storage
from .utils import content_hash, extract_text, fetch_page, is_valid_url, normalize_url

console = Console()
monitor = Monitor()
storage = Storage()


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


@click.group()
@click.version_option(version=__version__, prog_name="pagewatch")
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)


@cli.command()
def init():
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

    watch = storage.add_watch(name=name, url=url, selector=selector, interval=interval)
    console.print(f"[green]Added watch:[/] {name}")
    console.print(f"  URL:      {url}")
    if selector:
        console.print(f"  Selector: {selector}")
    console.print(f"  Interval: {interval}s")


@cli.command()
def list():
    watches = storage.load_watches()
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
        last = w.get("last_checked", "Never")
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
def check(name):
    if name:
        watches = storage.load_watches()
        watch = next((w for w in watches if w["name"] == name), None)
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


@cli.command()
@click.argument("name")
def diff(name):
    watches = storage.load_watches()
    watch = next((w for w in watches if w["name"] == name), None)
    if not watch:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)

    snapshot = storage.load_snapshot(name)
    if not snapshot or not snapshot.get("history"):
        console.print(f"[yellow]No snapshot history for '{name}'. Run 'pagewatch check' first.[/]")
        return

    history = snapshot["history"]
    console.print(f"[dim]Snapshot history: {len(history)} entries[/]")

    if len(history) < 2:
        latest = history[-1]
        console.print(f"[yellow]Only one snapshot available (from {latest['timestamp']}). No diff possible yet.[/]")
        return

    latest_snap = history[-1]
    prev_snap = history[-2]

    latest_text = snapshot.get("latest", {}).get("full_text", "")
    if not latest_text:
        console.print("[red]Latest snapshot text is empty.[/]")
        return

    console.print(f"\n[bold]Diff for {name}[/]")
    console.print(f"[dim]  Latest:  {latest_snap['timestamp']}[/]")
    console.print(f"[dim]  Previous: {prev_snap['timestamp']}[/]")
    console.print()

    old_text = ""
    from .utils import compute_diff
    for i in range(len(history) - 2, -1, -1):
        if history[i].get("content_hash") != latest_snap.get("content_hash"):
            prev_snap_data = storage.load_snapshot(name)
            if prev_snap_data:
                old_text = prev_snap_data.get("latest", {}).get("full_text", "")
            if not old_text:
                old_text = ""
            break

    diff_output = compute_diff(old_text, latest_text)
    if diff_output:
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
    else:
        console.print("[yellow]No differences found.[/]")


@cli.command()
def config():
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
    if storage.remove_watch(name):
        console.print(f"[green]Removed watch: {name}[/]")
    else:
        console.print(f"[red]Watch '{name}' not found.[/]")
        sys.exit(1)


if __name__ == "__main__":
    print_banner()
    cli()