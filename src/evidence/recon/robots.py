"""robots.txt parsing. A Disallow line is free reconnaissance about endpoint
structure — record every one as a discovered path candidate, per Part 1B of
docs/evidence-gathering-plan.md. Whether we ever *call* one is a separate,
later policy decision (RobotsGate), not this module's job.
"""

from __future__ import annotations


def extract_disallow_paths(robots_txt: str) -> list[str]:
    """Every distinct Disallow value across all user-agent groups, in file order."""
    paths: list[str] = []
    for line in (robots_txt or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        directive, _, value = line.partition(":")
        if directive.strip().lower() == "disallow":
            value = value.strip()
            if value and value not in paths:
                paths.append(value)
    return paths


def parse_robots(robots_txt: str, user_agent: str) -> dict:
    """Parse raw robots.txt into the recon fields Prompt 1 needs: sitemap
    references, crawl-delay for our UA and for *, and discovered paths.
    """
    from protego import Protego

    parsed = Protego.parse(robots_txt or "")
    return {
        "sitemaps": list(parsed.sitemaps),
        "crawl_delay_for_us": parsed.crawl_delay(user_agent),
        "crawl_delay_for_star": parsed.crawl_delay("*"),
        "discovered_path_candidates": extract_disallow_paths(robots_txt),
    }
