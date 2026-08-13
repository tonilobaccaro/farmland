"""CLI entrypoint for the evidence-gathering harness."""

from __future__ import annotations

import asyncio
import importlib
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from evidence.artifacts import ArtifactWriter
from evidence.config import RunConfig, SiteConfig, load_targets
from evidence.phases.base import PhaseContext, all_phases, get_phase, resolve_order
from evidence.politeness import RateLimiter, RequestBudget, RobotsGate

app = typer.Typer(add_completion=False, help="Evidence-gathering harness for farmland listing sites.")
console = Console()

DEFAULT_TARGETS_PATH = Path("targets.yaml")

# Modules that register phases as a side effect of import. Later prompts add
# more phase modules here; the CLI structure itself does not change.
PHASE_MODULES = [
    "evidence.phases.p1_recon",
    "evidence.phases.p1b_passive",
    "evidence.phases.p2_static",
]


def _load_phase_modules() -> None:
    for mod in PHASE_MODULES:
        importlib.import_module(mod)


def _sites(wave: int | None = None, targets_path: Path = DEFAULT_TARGETS_PATH) -> list[SiteConfig]:
    sites = load_targets(targets_path)
    if wave is not None:
        sites = [s for s in sites if s.wave == wave]
    return sites


def _site_by_slug(slug: str, targets_path: Path = DEFAULT_TARGETS_PATH) -> SiteConfig:
    for s in load_targets(targets_path):
        if s.slug == slug:
            return s
    raise typer.BadParameter(f"no site with slug {slug!r} in {targets_path}")


@app.command("list-sites")
def list_sites(
    wave: int | None = typer.Option(None, "--wave", help="Filter to a single wave (1-4)."),
    targets: Path = typer.Option(DEFAULT_TARGETS_PATH, "--targets", help="Path to targets.yaml"),
) -> None:
    """Print all sites grouped by wave."""
    sites = _sites(targets_path=targets)
    by_wave: dict[int, list[SiteConfig]] = {}
    for s in sites:
        if wave is not None and s.wave != wave:
            continue
        by_wave.setdefault(s.wave, []).append(s)

    for w in sorted(by_wave):
        table = Table(title=f"Wave {w}")
        table.add_column("slug")
        table.add_column("base_url")
        table.add_column("archetype")
        table.add_column("expected_family")
        for s in by_wave[w]:
            table.add_row(s.slug, s.base_url, s.archetype, s.expected_family)
        console.print(table)


def _parse_phases_option(phases: str | None, all_phases_flag: bool) -> list[str]:
    _load_phase_modules()
    if all_phases_flag:
        return resolve_order()
    if phases is None:
        return []
    if phases.strip().lower() == "none":
        return []
    names = [p.strip() for p in phases.split(",") if p.strip()]
    return resolve_order(names)


async def _run_site(
    site: SiteConfig,
    run_config: RunConfig,
    phase_names: list[str],
    force: bool,
) -> None:
    artifacts = ArtifactWriter(site.slug, run_config.evidence_root)
    artifacts.scaffold()

    robots_txt = None
    robots_path = "01_policy/robots.txt"
    if artifacts.exists(robots_path):
        robots_txt = (artifacts.site_root / robots_path).read_text(encoding="utf-8")

    ctx = PhaseContext(
        site=site,
        run_config=run_config,
        artifacts=artifacts,
        rate_limiter=RateLimiter(run_config.min_delay_s, run_config.max_delay_s),
        budget=RequestBudget(run_config.request_budget),
        robots=RobotsGate(robots_txt, run_config.user_agent, run_config.respect_robots),
    )

    for name in phase_names:
        result_rel = f"00_meta/phases/{name}.json"
        if not force and artifacts.exists(result_rel):
            prior = artifacts.read_json(result_rel)
            if prior.get("status") == "ok":
                console.print(f"[dim]{site.slug}/{name}: skipped (already ok)[/dim]")
                continue

        phase_cls = get_phase(name)
        phase = phase_cls()
        started = datetime.now(UTC)
        try:
            result = await phase.run(ctx)
        except Exception as exc:  # noqa: BLE001 - a phase failure is a recorded result, not a crash
            finished = datetime.now(UTC)
            from evidence.models import PhaseResult

            result = PhaseResult(
                phase=name,
                site=site.slug,
                started_at=started,
                finished_at=finished,
                status="failed",
                error=str(exc),
            )
        artifacts.json(result_rel, result.model_dump(mode="json"))
        console.print(f"{site.slug}/{name}: [bold]{result.status}[/bold]")


@app.command("run")
def run(
    site: str = typer.Option(..., "--site", help="Site slug from targets.yaml"),
    phases: str | None = typer.Option(
        None, "--phases", help='Comma-separated phase names, or "none" for scaffold only.'
    ),
    all_phases_flag: bool = typer.Option(False, "--all-phases", help="Run every registered phase."),
    force: bool = typer.Option(False, "--force", help="Re-run phases even if already ok."),
    targets: Path = typer.Option(DEFAULT_TARGETS_PATH, "--targets"),
    evidence_root: Path = typer.Option(None, "--evidence-root", help="Override RunConfig.evidence_root"),
) -> None:
    """Run one or more phases for a single site."""
    site_cfg = _site_by_slug(site, targets_path=targets)
    run_config = RunConfig(evidence_root=evidence_root) if evidence_root else RunConfig()
    phase_names = _parse_phases_option(phases, all_phases_flag)
    asyncio.run(_run_site(site_cfg, run_config, phase_names, force))


@app.command("run-wave")
def run_wave(
    wave: int = typer.Argument(..., help="Wave number (1-4)"),
    phases: str | None = typer.Option(None, "--phases"),
    all_phases_flag: bool = typer.Option(False, "--all-phases"),
    force: bool = typer.Option(False, "--force"),
    targets: Path = typer.Option(DEFAULT_TARGETS_PATH, "--targets"),
    evidence_root: Path = typer.Option(None, "--evidence-root"),
) -> None:
    """Run one or more phases for every site in a wave."""
    sites = _sites(wave=wave, targets_path=targets)
    if not sites:
        console.print(f"[yellow]no sites found for wave {wave}[/yellow]")
        raise typer.Exit(code=1)
    run_config = RunConfig(evidence_root=evidence_root) if evidence_root else RunConfig()
    phase_names = _parse_phases_option(phases, all_phases_flag)
    for site_cfg in sites:
        asyncio.run(_run_site(site_cfg, run_config, phase_names, force))


@app.command("status")
def status(
    targets: Path = typer.Option(DEFAULT_TARGETS_PATH, "--targets"),
    evidence_root: Path = typer.Option(Path("./evidence"), "--evidence-root"),
) -> None:
    """Table of site x phase x status, read from PhaseResults on disk."""
    _load_phase_modules()
    sites = _sites(targets_path=targets)
    phase_names = sorted(all_phases().keys())

    table = Table(title="Evidence status")
    table.add_column("site")
    for p in phase_names:
        table.add_column(p)

    for s in sites:
        artifacts = ArtifactWriter(s.slug, evidence_root)
        row = [s.slug]
        for p in phase_names:
            rel = f"00_meta/phases/{p}.json"
            if artifacts.exists(rel):
                row.append(artifacts.read_json(rel).get("status", "?"))
            else:
                row.append("-")
        table.add_row(*row)
    console.print(table)


@app.command("doctor")
def doctor(
    evidence_root: Path = typer.Option(Path("./evidence"), "--evidence-root"),
) -> None:
    """Preflight environment check before a long local run."""
    checks: list[tuple[str, bool, str]] = []

    import sys

    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python >= 3.11", py_ok, f"found {sys.version.split()[0]}; install 3.11+"))

    required_imports = [
        "httpx",
        "curl_cffi",
        "playwright",
        "selectolax",
        "lxml",
        "extruct",
        "protego",
        "tldextract",
        "w3lib",
        "jsonpath_ng",
        "pdfplumber",
        "jinja2",
        "pandas",
        "pydantic",
        "typer",
        "rich",
        "yaml",
    ]
    for mod in required_imports:
        try:
            importlib.import_module(mod)
            checks.append((f"import {mod}", True, ""))
        except ImportError as exc:
            checks.append((f"import {mod}", False, f"pip install -e '.[dev]' ({exc})"))

    chromium_ok = False
    chromium_hint = "run `playwright install chromium`"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        chromium_ok = True
    except Exception as exc:  # noqa: BLE001
        chromium_hint = f"run `playwright install chromium` ({exc})"
    checks.append(("Chromium launchable", chromium_ok, chromium_hint))

    dns_ok = False
    try:
        socket.getaddrinfo("web.archive.org", 443)
        dns_ok = True
    except OSError as exc:
        dns_hint = f"DNS resolution failed: {exc}"
    else:
        dns_hint = ""
    checks.append(("DNS resolves", dns_ok, dns_hint or "check network/DNS config"))

    https_ok = False
    https_hint = "check outbound HTTPS / firewall / proxy config"
    try:
        import httpx

        resp = httpx.get("https://www.google.com", timeout=10.0)
        https_ok = resp.status_code < 500
    except Exception as exc:  # noqa: BLE001
        https_hint = f"outbound HTTPS failed: {exc}"
    checks.append(("Outbound HTTPS reachable", https_ok, https_hint))

    evidence_root.mkdir(parents=True, exist_ok=True)
    disk_free_gb = shutil.disk_usage(evidence_root).free / (1024**3)
    disk_ok = disk_free_gb >= 20
    checks.append(
        (
            "Free disk >= 20GB",
            disk_ok,
            f"only {disk_free_gb:.1f}GB free at {evidence_root}; free space or use --evidence-root",
        )
    )

    write_ok = False
    write_hint = f"cannot write to {evidence_root}; check permissions"
    try:
        probe = evidence_root / ".doctor_write_probe"
        probe.write_text("ok")
        probe.unlink()
        write_ok = True
        write_hint = ""
    except OSError as exc:
        write_hint = f"cannot write to {evidence_root}: {exc}"
    checks.append(("Write permission on evidence_root", write_ok, write_hint))

    table = Table(title="evidence doctor")
    table.add_column("check")
    table.add_column("result")
    table.add_column("fix hint")
    all_ok = True
    for name, ok, hint in checks:
        all_ok = all_ok and ok
        table.add_row(name, "[green]pass[/green]" if ok else "[red]FAIL[/red]", "" if ok else hint)
    console.print(table)

    if not all_ok:
        raise typer.Exit(code=1)


# NOTE: `evidence validate --site SLUG` (Prompt 6) and `evidence report [--site SLUG]`
# (Prompt 6) are intentionally not implemented yet. Adding them later is just two
# more @app.command() functions in this module; nothing else needs to change.


if __name__ == "__main__":
    app()
