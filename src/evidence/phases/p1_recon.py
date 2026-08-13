"""P1 — passive recon: DNS/TLS, robots.txt, sitemaps, well-knowns, legal pages.
No JavaScript, no escalation ladder — see p2_static for that.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from evidence.fetch import Fetcher
from evidence.models import FetchTier, PhaseResult
from evidence.phases.base import Phase, PhaseContext, register
from evidence.recon.dns_tls import DnsRecords, fetch_tls_info, resolve_dns
from evidence.recon.legal import find_legal_links
from evidence.recon.robots import parse_robots
from evidence.recon.sitemap import walk_sitemaps
from evidence.recon.wellknown import probe_wellknown, slugify_path

MAX_LEGAL_SNAPSHOTS = 5


@register("p1_recon")
class P1Recon(Phase):
    depends_on: ClassVar[list[str]] = []

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        started = datetime.now(UTC)
        artifacts = ctx.artifacts
        base_url = ctx.site.base_url
        hostname = urlparse(base_url).hostname or ctx.site.slug
        notes: list[str] = []
        written: list[str] = []
        requests_before = ctx.budget.used

        fetcher = Fetcher(
            user_agent=ctx.run_config.user_agent,
            rate_limiter=ctx.rate_limiter,
            budget=ctx.budget,
            robots=ctx.robots,
            artifacts=artifacts,
            timeout_s=ctx.run_config.timeout_s,
        )

        # 1. robots.txt -------------------------------------------------
        robots_url = urljoin(base_url, "/robots.txt")
        robots_result, robots_txt = await fetcher.fetch_text(
            robots_url, FetchTier.L0, body_rel="01_policy/robots.txt"
        )
        robots_txt = robots_txt or ""
        parsed_robots = parse_robots(robots_txt, ctx.run_config.user_agent)
        written.append(artifacts.json("01_policy/robots_parsed.json", parsed_robots))
        if robots_result.body_path:
            written.append(robots_result.body_path)
        else:
            notes.append(f"robots.txt not fetched: {robots_result.error or robots_result.status}")
        # Once we know real policy, gate every later fetch in this run by it —
        # including the rest of this phase.
        ctx.robots.reload(robots_txt)

        # 2. DNS + TLS ----------------------------------------------------
        dns_dict: dict = DnsRecords(hostname=hostname, errors={"skipped": "no hostname"}).to_dict()
        tls_dict: dict | None = None
        tls_error: str | None = None
        try:
            dns_records = await resolve_dns(hostname)
            dns_dict = dns_records.to_dict()
        except Exception as exc:  # noqa: BLE001 - DNS failures are a recorded finding
            dns_dict["errors"]["resolve_dns"] = f"{type(exc).__name__}: {exc}"
            notes.append(f"DNS resolution failed: {exc}")
        try:
            tls_info = await asyncio.to_thread(fetch_tls_info, hostname)
            tls_dict = tls_info.to_dict()
        except Exception as exc:  # noqa: BLE001
            tls_error = f"{type(exc).__name__}: {exc}"
            notes.append(f"TLS handshake failed: {exc}")
        written.append(artifacts.json("00_meta/dns_tls.json", {"dns": dns_dict, "tls": tls_dict, "tls_error": tls_error}))

        # 3. Sitemaps -------------------------------------------------------
        seed_sitemaps = list(parsed_robots["sitemaps"]) or [urljoin(base_url, "/sitemap.xml")]
        counter = {"n": 0}

        async def _fetch_sitemap_text(url: str) -> str | None:
            counter["n"] += 1
            rel = f"01_policy/sitemaps/raw/{counter['n']:03d}.xml"
            _result, text = await fetcher.fetch_text(url, FetchTier.L0, body_rel=rel)
            return text

        sitemap_result = await walk_sitemaps(seed_sitemaps, _fetch_sitemap_text)
        written.append(artifacts.json("01_policy/sitemaps/tree.json", sitemap_result["tree"]))
        written.append(
            artifacts.json(
                "01_policy/sitemaps/url_patterns.json",
                {
                    "url_path_template_counts": sitemap_result["url_path_template_counts"],
                    "total_urls": sitemap_result["total_urls"],
                    "lastmod_all_identical": sitemap_result["lastmod_all_identical"],
                    "lastmod_distinct_count": sitemap_result["lastmod_distinct_count"],
                    "lastmod_sample": sitemap_result["lastmod_sample"],
                    "capped_by_files": sitemap_result["capped_by_files"],
                    "capped_by_urls": sitemap_result["capped_by_urls"],
                },
            )
        )
        if sitemap_result["total_urls"] == 0:
            notes.append("no sitemap URLs discovered across all seed sitemaps (recorded, not silently missing)")

        # 4. Well-knowns ------------------------------------------------
        wellknown_results = await probe_wellknown(base_url, fetcher)
        written.append(artifacts.json("01_policy/wellknown.json", wellknown_results))
        for r in wellknown_results.values():
            if r["found"] and r["body_path"]:
                written.append(r["body_path"])

        # 5. Legal pages ------------------------------------------------
        _home_result, home_text = await fetcher.fetch_text(
            base_url, FetchTier.L0, body_rel="01_policy/_home_for_legal_scan.html"
        )
        legal_links = find_legal_links(home_text or "", base_url)
        snapshots = []
        for link in legal_links[:MAX_LEGAL_SNAPSHOTS]:
            slug = slugify_path(urlparse(link["url"]).path or "legal")
            html_rel = f"01_policy/legal/{slug}.html"
            text_rel = f"01_policy/legal/{slug}.txt"
            _result, page_text = await fetcher.fetch_text(link["url"], FetchTier.L0, body_rel=html_rel)
            if page_text is not None:
                extracted = HTMLParser(page_text).text(separator="\n").strip()
                artifacts.text(text_rel, extracted)
                snapshots.append({**link, "html_path": html_rel, "text_path": text_rel})
                written += [html_rel, text_rel]
            else:
                snapshots.append({**link, "html_path": None, "text_path": None, "note": "fetch_failed"})
        written.append(
            artifacts.json("01_policy/legal_pages.json", {"discovered_links": legal_links, "snapshots": snapshots})
        )
        if not legal_links:
            notes.append("no ToS/Privacy/Legal/Copyright links found on the homepage footer scan")

        finished = datetime.now(UTC)
        status = "partial" if (tls_error or dns_dict.get("errors")) else "ok"
        return PhaseResult(
            phase=self.name,
            site=ctx.site.slug,
            started_at=started,
            finished_at=finished,
            status=status,
            artifacts=written,
            requests_made=ctx.budget.used - requests_before,
            notes=notes,
        )
