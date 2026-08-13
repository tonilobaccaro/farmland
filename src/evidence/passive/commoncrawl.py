"""Common Crawl index: URL-pattern confirmation at scale, and a fallback
archived-HTML source when Wayback is thin. Queries are slow — the calling
phase caches raw index responses to disk (see phases/p1b_passive.py) rather
than hitting index.commoncrawl.org more than once per site per run.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

FetchText = Callable[[str], Awaitable[str | None]]

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"


def parse_collinfo(text: str) -> list[str]:
    """Extract crawl ids from collinfo.json, newest first (as CC returns them)."""
    try:
        collections = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    return [c["id"] for c in collections if isinstance(c, dict) and "id" in c]


def build_cc_cdx_url(crawl_id: str, domain: str) -> str:
    return f"https://index.commoncrawl.org/{crawl_id}-index?url={domain}/*&output=json"


def parse_cc_ndjson(text: str) -> list[dict]:
    """Common Crawl's index is newline-delimited JSON, one record per line."""
    records = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


async def get_recent_crawl_ids(fetch_text: FetchText, n: int = 3) -> list[str]:
    text = await fetch_text(COLLINFO_URL)
    if text is None:
        return []
    return parse_collinfo(text)[:n]


async def query_recent_crawls(domain: str, fetch_text: FetchText, n: int = 3) -> dict:
    """Query the n most recent Common Crawl indexes for this domain."""
    crawl_ids = await get_recent_crawl_ids(fetch_text, n=n)
    results: dict[str, dict] = {}
    total_urls = 0

    for crawl_id in crawl_ids:
        url = build_cc_cdx_url(crawl_id, domain)
        text = await fetch_text(url)
        if text is None:
            results[crawl_id] = {"url_count": 0, "sample_urls": [], "error": "fetch_failed"}
            continue
        records = parse_cc_ndjson(text)
        sample_urls = [r["url"] for r in records[:20] if "url" in r]
        results[crawl_id] = {"url_count": len(records), "sample_urls": sample_urls}
        total_urls += len(records)

    return {"crawl_ids_queried": crawl_ids, "results": results, "total_urls": total_urls}
