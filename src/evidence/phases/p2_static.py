"""P2 — static fetch + escalation ladder, per page class.

Escalates on home (always), search (if seed_search_url is configured), and
one detail page (if seed_detail_urls is configured), fingerprinting WAF and
tech from each response. fetch_ladder.json records the minimum working tier
per page class because sites commonly serve the homepage freely and
challenge only search — see docs/build-prompts.md Prompt 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from evidence.blocked import build_blocked_report
from evidence.fetch import Fetcher
from evidence.fingerprint.tech import fingerprint_tech, wp_json_hint
from evidence.fingerprint.waf import fingerprint_waf
from evidence.models import FetchTier, PhaseResult
from evidence.phases.base import Phase, PhaseContext, register

PAGE_CLASS_BODY_PATH = {
    "home": "02_rendering/home_raw.html",
    "search": "05_listing_pages/search_p1_raw.html",
    "detail": "06_detail_pages/sample_seed/raw.html",
}


def _extract_script_srcs(html: str) -> list[str]:
    tree = HTMLParser(html or "")
    return [s.attributes.get("src") for s in tree.css("script[src]") if s.attributes.get("src")]


@register("p2_static")
class P2Static(Phase):
    depends_on: ClassVar[list[str]] = ["p1_recon"]

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        started = datetime.now(UTC)
        artifacts = ctx.artifacts
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

        page_urls: dict[str, str] = {"home": ctx.site.base_url}
        if ctx.site.seed_search_url:
            page_urls["search"] = ctx.site.seed_search_url
        if ctx.site.seed_detail_urls:
            page_urls["detail"] = ctx.site.seed_detail_urls[0]
        else:
            notes.append("no seed_detail_urls configured for this site; detail page class skipped")
        if "search" not in page_urls:
            notes.append("no seed_search_url configured for this site; search page class skipped")

        headers_by_class: dict[str, dict] = {}
        waf_by_class: dict[str, dict] = {}
        tech_by_class: dict[str, list] = {}
        fetch_ladder: dict[str, dict] = {}
        by_page_class_for_blocked: dict[str, dict] = {}

        for page_class, url in page_urls.items():
            body_rel = PAGE_CLASS_BODY_PATH[page_class]
            result, tier = await fetcher.escalate(url, max_tier=FetchTier.L2, body_rel=body_rel)

            text = None
            if result.body_path:
                text = (artifacts.site_root / result.body_path).read_text(encoding="utf-8", errors="replace")
                written.append(result.body_path)

            headers_by_class[page_class] = result.headers
            waf_fp = fingerprint_waf(result.headers, result.set_cookies, text or "", result.status)
            waf_by_class[page_class] = waf_fp.to_dict()

            script_srcs = _extract_script_srcs(text or "")
            tech_matches = fingerprint_tech(text or "", result.headers, result.set_cookies, script_srcs)
            tech_by_class[page_class] = [
                {"technology": m.technology, "confidence": m.confidence, "evidence": m.evidence}
                for m in tech_matches
            ]

            reachable = tier != FetchTier.BLOCKED
            fetch_ladder[page_class] = {
                "tier": tier.value if reachable else None,
                "status": result.status,
                "reachable": reachable,
            }
            by_page_class_for_blocked[page_class] = {
                "reachable": reachable,
                "tier": tier.value if reachable else None,
                "status": result.status,
                "vendor": waf_fp.vendor,
                "signal": waf_fp.matches[0].signal if waf_fp.matches else None,
                "highest_tier_tried": FetchTier.L2.value,
            }
            if not reachable:
                notes.append(
                    f"{page_class} page blocked at every tier up to L2 "
                    f"(status={result.status}, vendor={waf_fp.vendor})"
                )

        # WordPress is the one technology this phase probes for live (per Prompt
        # 1's acceptance criteria: "wp-json responds" is a real, budgeted request,
        # separate from the pure no-network fingerprint_tech classifier).
        home_tech_names = {m["technology"] for m in tech_by_class.get("home", [])}
        if "WordPress" in home_tech_names:
            wp_json_url = urljoin(ctx.site.base_url, "/wp-json/")
            wp_result = await fetcher.fetch(wp_json_url, FetchTier.L0)
            reachable_wp = wp_result.status is not None and wp_result.status < 400
            tech_by_class["home"].append(
                {
                    "technology": "WordPress",
                    "confidence": 0.99 if reachable_wp else 0.5,
                    "evidence": wp_json_hint(reachable_wp),
                }
            )

        written.append(artifacts.json("00_meta/headers.json", headers_by_class))
        written.append(artifacts.json("00_meta/tech_fingerprint.json", tech_by_class))
        written.append(artifacts.json("00_meta/waf.json", waf_by_class))
        written.append(artifacts.json("00_meta/fetch_ladder.json", fetch_ladder))

        still_accessible: list[str] = []
        try:
            sitemap_patterns = artifacts.read_json("01_policy/sitemaps/url_patterns.json")
            if sitemap_patterns.get("total_urls", 0) > 0:
                still_accessible.append("sitemap")
        except FileNotFoundError:
            pass
        blocked_report = build_blocked_report(by_page_class_for_blocked, extra_still_accessible=still_accessible)
        written.append(artifacts.json("00_meta/blocked_report.json", blocked_report))

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
