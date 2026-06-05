"""
main.py — SAP PI/PO → CPI Migration Scaffolder CLI

Usage examples:
  python main.py --env cf --targets s4hana_cloud,ariba --output ./output
  python main.py --env cf --output ./output
  python main.py --list-targets
  python main.py --refresh-cache --targets s4hana_cloud
  python main.py --reports-only --output ./output
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.progress import track

from auth.authenticator import build_pi_authenticator
from extractor.pi_extractor import build_extractor
from analyzer.complexity_analyzer import ComplexityAnalyzer
from scaffolder.iflow_scaffolder import IFlowScaffolder
from reporter.report_generator import ReportGenerator
from destinations.registry import DESTINATION_REGISTRY, list_targets as get_all_targets
from destinations.hub_fetcher import HubFetcher
from destinations.resolver import DestinationResolver

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cpi_migrator")


@click.command()
@click.option("--env",           default=None,                   help="Override environment: cf or neo")
@click.option("--config",        default="config/settings.yaml", show_default=True)
@click.option("--output",        default="./output",             show_default=True)
@click.option("--targets",       default=None,
              help="Comma-separated destination IDs, e.g. s4hana_cloud,ariba,successfactors")
@click.option("--reports-only",  is_flag=True, help="Skip iFlow scaffolding, reports only")
@click.option("--refresh-cache", is_flag=True, help="Force-refresh Hub cache and exit")
@click.option("--list-targets",  is_flag=True, help="Show all available destination targets and exit")
@click.option("--cache-status",  is_flag=True, help="Show Hub cache status and exit")
@click.option("--verbose",       is_flag=True, help="Enable debug logging")
def cli(env, config, output, targets, reports_only, refresh_cache,
        list_targets, cache_status, verbose):
    """SAP PI/PO → CPI Migration Scaffolder with live destination registry."""

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Shortcut: list targets ───────────────────────────────────────
    if list_targets:
        _print_targets()
        return

    # ── Load config ──────────────────────────────────────────────────
    cfg_path = Path(config)
    if not cfg_path.exists():
        console.print(f"[red]Config not found: {cfg_path}[/red]")
        console.print("Copy config/settings.yaml.example → config/settings.yaml and fill in credentials.")
        sys.exit(1)

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    if env:
        cfg["environment"] = env

    # ── Resolve target IDs ───────────────────────────────────────────
    if targets:
        target_ids = [t.strip() for t in targets.split(",") if t.strip()]
    else:
        target_ids = cfg.get("destinations", {}).get("targets", ["s4hana_cloud"])

    for tid in target_ids:
        if tid not in DESTINATION_REGISTRY:
            console.print(f"[red]Unknown target '{tid}'.[/red] Run --list-targets to see options.")
            sys.exit(1)

    # ── Build Hub fetcher ────────────────────────────────────────────
    dest_cfg    = cfg.get("destinations", {})
    hub_api_key = dest_cfg.get("hub_api_key") or None
    cache_ttl   = int(dest_cfg.get("cache_ttl_hours", 24)) * 3600
    cache_dir   = dest_cfg.get("cache_dir") or None
    fetcher     = HubFetcher(cache_dir=cache_dir, default_ttl=cache_ttl, hub_api_key=hub_api_key)

    # ── Shortcut: cache status ───────────────────────────────────────
    if cache_status:
        _print_cache_status(fetcher)
        return

    # ── Shortcut: force refresh ──────────────────────────────────────
    if refresh_cache:
        _do_refresh(fetcher, target_ids, cache_ttl)
        return

    # ── Banner ───────────────────────────────────────────────────────
    console.rule("[bold blue]SAP PI/PO → CPI Migration Scaffolder[/bold blue]")
    console.print(f"Environment  : [cyan]{cfg.get('environment', 'cf').upper()}[/cyan]")
    console.print(f"Destinations : [cyan]{', '.join(target_ids)}[/cyan]")
    console.print(f"Output dir   : [cyan]{output}[/cyan]\n")

    # ── Startup Hub refresh (stale packages only) ────────────────────
    if dest_cfg.get("refresh_on_startup", True):
        all_pkg_ids = list({
            src.package_id
            for tid in target_ids
            for src in DESTINATION_REGISTRY[tid].hub_sources
        })
        stale_pkgs = [
            pid for pid in all_pkg_ids
            if not _cache_entry_fresh(fetcher, pid, cache_ttl)
        ]
        if stale_pkgs:
            console.print(f"[dim]Refreshing {len(stale_pkgs)} stale Hub package(s) in background …[/dim]")
            try:
                asyncio.run(fetcher.refresh_all_async(stale_pkgs, ttl=cache_ttl))
            except Exception as exc:
                console.print(f"[yellow]Hub refresh warning: {exc} — will use cached data.[/yellow]")

    # ── Step 1: Extract PI/PO interfaces ────────────────────────────
    console.print("[bold]Step 1/5[/bold] Extracting PI/PO interfaces …")
    try:
        pi_auth    = build_pi_authenticator(cfg)
        pi_session = pi_auth.get_session()
    except Exception:
        pi_session = None

    try:
        extractor = build_extractor(cfg, pi_session)
        records   = extractor.extract_all()
    except Exception as exc:
        console.print(f"[red]Extraction failed: {exc}[/red]")
        console.print("[dim]Tip: Set pi.export_file in settings.yaml to use an Excel export.[/dim]")
        sys.exit(1)

    if not records:
        console.print("[yellow]No interfaces found. Check PI/PO connection or export file.[/yellow]")
        sys.exit(0)

    console.print(f"  Found [green]{len(records)}[/green] interfaces.\n")

    # ── Step 2: Analyze complexity ───────────────────────────────────
    console.print("[bold]Step 2/5[/bold] Analyzing complexity …")
    analyzer    = ComplexityAnalyzer(cfg)
    assessments = analyzer.assess_all(records)
    _print_complexity_table(assessments)

    # ── Step 3: Resolve destinations ────────────────────────────────
    console.print(f"\n[bold]Step 3/5[/bold] Resolving against {len(target_ids)} destination target(s) …")
    resolver    = DestinationResolver(fetcher=fetcher)
    resolutions = resolver.resolve_all(assessments, target_ids)
    _print_resolution_summary(resolutions, target_ids)

    # ── Step 4: Scaffold iFlows ──────────────────────────────────────
    if not reports_only:
        console.print(f"\n[bold]Step 4/5[/bold] Scaffolding iFlow XML stubs …")
        templates_dir = str(Path(__file__).parent / "templates")
        scaffolder    = IFlowScaffolder(templates_dir=templates_dir, output_dir=output)
        iflow_paths   = []
        for a in track(assessments, description="Scaffolding …"):
            iflow_paths.append(scaffolder.scaffold(a))
        console.print(f"  ✓ {len(iflow_paths)} iFlow stubs → [cyan]{output}/iflows/[/cyan]")
    else:
        console.print("\n[bold]Step 4/5[/bold] [dim]Skipped (--reports-only)[/dim]")

    # ── Step 5: Reports ──────────────────────────────────────────────
    console.print(f"\n[bold]Step 5/5[/bold] Generating reports → {output}")
    reporter = ReportGenerator(output_dir=output)
    xl_path  = reporter.generate_excel(assessments, resolutions=resolutions, target_ids=target_ids)
    md_path  = reporter.generate_markdown(assessments, resolutions=resolutions, target_ids=target_ids)
    console.print(f"  ✓ Excel gap report  → [cyan]{xl_path}[/cyan]")
    console.print(f"  ✓ Markdown summary  → [cyan]{md_path}[/cyan]")

    console.rule()
    console.print("[bold green]Done![/bold green] Review HIGH complexity and ⚠ warned interfaces first.")
    console.print("Import .iflw files: BTP Cockpit → Integration Suite → Design → Import.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cache_entry_fresh(fetcher: HubFetcher, package_id: str, ttl: int) -> bool:
    from destinations.hub_fetcher import CacheEntry
    entry = CacheEntry(fetcher.cache_dir, f"pkg_{package_id}")
    return entry.is_fresh(ttl)


def _print_targets():
    table = Table(title="Available destination targets", show_header=True, header_style="bold blue")
    table.add_column("ID",          style="cyan",  no_wrap=True, width=18)
    table.add_column("Label",       style="white", width=38)
    table.add_column("Variant",     style="dim",   width=12)
    table.add_column("Adapters",    width=40)
    table.add_column("Hub sources", justify="right", width=12)
    for t in DESTINATION_REGISTRY.values():
        table.add_row(
            t.id, t.label, t.variant,
            ", ".join(t.supported_adapters[:5]) + ("…" if len(t.supported_adapters) > 5 else ""),
            str(len(t.hub_sources)),
        )
    console.print(table)


def _print_cache_status(fetcher: HubFetcher):
    status = fetcher.cache_status()
    if not status:
        console.print("[yellow]Cache is empty.[/yellow] Run with --refresh-cache to populate.")
        return
    table = Table(title="Hub Cache Status", show_header=True, header_style="bold blue")
    table.add_column("Package",     style="cyan", width=35)
    table.add_column("Fresh?",      width=8)
    table.add_column("TTL left",    justify="right", width=12)
    table.add_column("Size",        justify="right", width=10)
    for s in status:
        fresh  = "✓ Yes" if s["fresh"] else "✗ No"
        colour = "green" if s["fresh"] else "red"
        ttl_h  = f"{s['ttl_remaining'] // 3600}h {(s['ttl_remaining'] % 3600) // 60}m"
        size_k = f"{s['size_bytes'] / 1024:.1f} KB"
        table.add_row(s["key"], f"[{colour}]{fresh}[/{colour}]", ttl_h, size_k)
    console.print(table)


def _do_refresh(fetcher: HubFetcher, target_ids: list[str], ttl: int):
    pkg_ids = list({
        src.package_id
        for tid in target_ids
        for src in DESTINATION_REGISTRY[tid].hub_sources
    })
    console.print(f"Force-refreshing {len(pkg_ids)} Hub package(s) …")
    results = asyncio.run(fetcher.refresh_all_async(pkg_ids, ttl=ttl))
    for pid, ok in results.items():
        icon, colour = ("✓", "green") if ok else ("✗", "red")
        console.print(f"  [{colour}]{icon}[/{colour}] {pid}")


def _print_complexity_table(assessments):
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Complexity",     width=12)
    table.add_column("Count",          justify="right", width=8)
    table.add_column("Effort (days)",  justify="right", width=14)
    for band, colour in [("LOW", "green"), ("MEDIUM", "yellow"), ("HIGH", "red")]:
        subset = [a for a in assessments if a.complexity == band]
        days   = sum(a.effort_days for a in subset)
        table.add_row(f"[{colour}]{band}[/{colour}]", str(len(subset)), f"{days:.1f}")
    total_days = sum(a.effort_days for a in assessments)
    table.add_row("[bold]TOTAL[/bold]", str(len(assessments)), f"[bold]{total_days:.1f}[/bold]")
    console.print(table)


def _print_resolution_summary(resolutions, target_ids):
    for tid in target_ids:
        label = getattr(DESTINATION_REGISTRY.get(tid), 'label', tid)
        warns = sum(
            1 for ir in resolutions.values()
            if tid in ir and ir[tid].compatibility_warnings
        )
        hub_hits = sum(
            1 for ir in resolutions.values()
            if tid in ir and ir[tid].hub_matches
        )
        console.print(
            f"  [cyan]{label}[/cyan] — "
            f"[yellow]{warns} warning(s)[/yellow], "
            f"[green]{hub_hits} Hub match(es)[/green]"
        )


if __name__ == "__main__":
    cli()
