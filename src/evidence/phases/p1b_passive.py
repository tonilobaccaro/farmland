"""P1b — no-touch evidence sources: Wayback, Common Crawl, crt.sh, syndication.

Must run for EVERY site, including ones p2_static found fully blocked — that
is precisely when it matters most, so this phase has no dependency on p2 and
must make ZERO requests to the target site's own origin. Every request here
goes to third-party public infrastructure (archive.org, crt.sh,
commoncrawl.org) instead — see _infra_get for why those are rate-limited and
budgeted like any other request, but deliberately not gated by the target
site's robots.txt.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urlparse

import httpx

from evidence.models import PhaseResult
from evidence.passive.archive import archive_coverage, build_snapshot_url, fetch_cdx_urls
from evidence.passive.commoncrawl import query_recent_crawls
from evidence.passive.ctlogs import extract_subdomains, fetch_subdomains, resolve_subdomains
from evidence.passive.serp import site_search
from evidence.passive.syndication import build_syndication_report, find_aggregator_badges
from evidence.phases.base import Phase, PhaseContext, register
from evidence.politeness import BudgetExhausted

MAX_SNAPSHOTS = 5  # seed search page + up to a few detail pages, from CDX results


@register("p1b_passive")
class P1bPassive(Phase):
    depends_on: ClassVar[list[str]] = []  # deliberately does not depend on p2_static

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        started = datetime.now(UTC)
        artifacts = ctx.artifacts
        notes: list[str] = []
        written: list[str] = []
        requests_before = ctx.budget.used

        domain = urlparse(ctx.site.base_url).hostname or ctx.site.slug

        async def infra_fetch_text(url: str) -> str | None:
            return await _infra_get(ctx, url)

        # 1. crt.sh subdomains --------------------------------------------
        crtsh_rows = await fetch_subdomains(domain, infra_fetch_text)
        subdomain_first_seen = extract_subdomains(crtsh_rows, domain)
        resolved = await resolve_subdomains(list(subdomain_first_seen.keys()))
        subdomains_report = {
            name: {"first_seen": first_seen, "resolves": resolved.get(name, False)}
            for name, first_seen in subdomain_first_seen.items()
        }
        written.append(artifacts.json("00_meta/subdomains.json", subdomains_report))
        if not subdomains_report:
            notes.append("crt.sh returned no subdomains (or the query failed) — recorded, not silently skipped")
        else:
            plausible = [n for n in subdomains_report if any(h in n for h in ("api", "idx", "search", "data", "mobile"))]
            if plausible:
                notes.append(f"plausible api/idx/search hosts from CT logs: {', '.join(plausible[:5])}")

        # 2. Wayback CDX + snapshots ---------------------------------------
        cdx_rows = await fetch_cdx_urls(domain, infra_fetch_text)
        coverage = archive_coverage(cdx_rows)

        seed_urls: list[str] = []
        if ctx.site.seed_search_url:
            seed_urls.append(ctx.site.seed_search_url)
        seed_urls += list(ctx.site.seed_detail_urls[:3])

        snapshot_manifest = []
        for seed_url in seed_urls[:MAX_SNAPSHOTS]:
            match = next((r for r in cdx_rows if r.get("original") == seed_url), None)
            if match is None:
                match = next((r for r in reversed(cdx_rows) if seed_url in (r.get("original") or "")), None)
            if match is None:
                snapshot_manifest.append({"seed_url": seed_url, "found": False})
                continue
            snap_url = build_snapshot_url(match["timestamp"], match["original"])
            text = await infra_fetch_text(snap_url)
            if text is None:
                snapshot_manifest.append(
                    {"seed_url": seed_url, "found": True, "fetched": False, "timestamp": match["timestamp"]}
                )
                continue
            slug = hashlib.sha1(seed_url.encode()).hexdigest()[:12]
            rel = f"01_policy/archive/snapshots/{slug}.html"
            artifacts.text(rel, text)
            written.append(rel)
            snapshot_manifest.append(
                {
                    "seed_url": seed_url,
                    "found": True,
                    "fetched": True,
                    "timestamp": match["timestamp"],
                    "snapshot_path": rel,
                    "source": "archive",
                }
            )

        written.append(
            artifacts.json(
                "01_policy/archive/manifest.json",
                {"coverage": coverage, "total_cdx_urls": len(cdx_rows), "snapshots": snapshot_manifest},
            )
        )
        if not cdx_rows:
            notes.append("no Wayback CDX records found for this domain")
        elif not seed_urls:
            notes.append("no seed_search_url/seed_detail_urls configured; CDX URL list captured but no snapshots fetched")

        # 3. Common Crawl ----------------------------------------------------
        cc_result = await query_recent_crawls(domain, infra_fetch_text)
        written.append(artifacts.json("01_policy/archive/commoncrawl.json", cc_result))

        # 4. Syndication — reuse whatever homepage HTML is already on disk
        #    (from p2_static) or, failing that, an archive snapshot. NEVER a
        #    fresh fetch of the site itself: this phase must be safe to run
        #    even when the site is fully blocked, with zero live requests to it.
        home_html: str | None = None
        home_source = None
        if artifacts.exists("02_rendering/home_raw.html"):
            home_html = (artifacts.site_root / "02_rendering/home_raw.html").read_text(
                encoding="utf-8", errors="replace"
            )
            home_source = "p2_static"
        else:
            home_snapshot = next(
                (
                    r
                    for r in reversed(cdx_rows)
                    if (r.get("original") or "").rstrip("/") == ctx.site.base_url.rstrip("/")
                ),
                None,
            )
            if home_snapshot:
                home_html = await infra_fetch_text(
                    build_snapshot_url(home_snapshot["timestamp"], home_snapshot["original"])
                )
                home_source = "archive" if home_html is not None else None

        badges = find_aggregator_badges(home_html or "", domain)
        syndication_report = build_syndication_report(badges)
        syndication_report["home_html_source"] = home_source
        if home_html is None:
            notes.append(
                "syndication check ran with no homepage HTML available "
                "(p2_static hadn't written one, and no Wayback homepage snapshot found)"
            )
        written.append(artifacts.json("00_meta/syndication.json", syndication_report))

        # 5. SERP — optional, off by default; no key configured means a clean skip.
        serp_result = await site_search(domain, backend_name=None, api_key=None)
        written.append(artifacts.json("01_policy/serp.json", serp_result))

        finished = datetime.now(UTC)
        return PhaseResult(
            phase=self.name,
            site=ctx.site.slug,
            started_at=started,
            finished_at=finished,
            status="ok",
            artifacts=written,
            requests_made=ctx.budget.used - requests_before,
            notes=notes,
        )


async def _infra_get(ctx: PhaseContext, url: str) -> str | None:
    """GET one third-party public-infrastructure URL: archive.org, crt.sh,
    commoncrawl.org, or (for the syndication fallback) an archive.org
    snapshot of the target site — never the target site's own origin
    directly. Rate-limited and budgeted like every other request in this
    harness, but NOT gated by the target site's RobotsGate: these are
    "public infrastructure serving exactly this purpose"
    (docs/evidence-gathering-plan.md, Part B-bis), governed by their own,
    unrelated robots policies.

    Cached to disk by URL hash — "apply the RateLimiter to them and cache
    aggressively" per Prompt 1b — so a retried phase in the same evidence
    tree doesn't re-query archive.org/crt.sh for URLs it already has.
    """
    artifacts = ctx.artifacts
    cache_rel = f"01_policy/_infra_cache/{hashlib.sha1(url.encode()).hexdigest()}.txt"
    if artifacts.exists(cache_rel):
        return (artifacts.site_root / cache_rel).read_text(encoding="utf-8", errors="replace")

    try:
        ctx.budget.consume()
    except BudgetExhausted:
        return None

    await ctx.rate_limiter.wait(url)

    try:
        async with httpx.AsyncClient(timeout=ctx.run_config.timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": ctx.run_config.user_agent})
    except Exception:  # noqa: BLE001 - an infra fetch failure is a recorded finding, not a crash
        return None

    ctx.rate_limiter.note_response(url, resp.status_code)
    if resp.status_code >= 400:
        return None

    artifacts.text(cache_rel, resp.text)
    return resp.text
