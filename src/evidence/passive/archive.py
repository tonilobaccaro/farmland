"""Wayback Machine: CDX URL enumeration + unrewritten snapshot fetches.

The CDX query alone often reveals a site's complete URL taxonomy — every
detail-page pattern it has ever used — with zero requests to the origin. The
`id_` suffix on a snapshot URL is essential: it returns the original
unmodified HTML, not Wayback's link-rewritten version, which would corrupt
the selectors we're trying to learn.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

FetchText = Callable[[str], Awaitable[str | None]]

CDX_BASE = "http://web.archive.org/cdx/search/cdx"


def build_cdx_url(domain: str, limit: int = 10000) -> str:
    return f"{CDX_BASE}?url={domain}/*&output=json&collapse=urlkey&limit={limit}"


def build_snapshot_url(timestamp: str, original_url: str) -> str:
    """The `id_` suffix returns unrewritten HTML — do not drop it."""
    return f"http://web.archive.org/web/{timestamp}id_/{original_url}"


def parse_cdx_json(text: str) -> list[dict]:
    """CDX `output=json` is a JSON array of arrays; the first row is the header."""
    try:
        rows = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    return [dict(zip(header, row, strict=False)) for row in rows[1:]]


def archive_coverage(snapshots: list[dict]) -> dict:
    """Summarize how well-archived a site is: count, date range, and monthly
    gaps within that range. A site archived every month for 10 years is one
    we can freely study from snapshots; a handful of scattered captures is not.
    """
    timestamps = sorted(s["timestamp"] for s in snapshots if s.get("timestamp"))
    if not timestamps:
        return {"count": 0, "first_seen": None, "last_seen": None, "distinct_months": 0, "gap_months": []}

    def to_year_month(ts: str) -> str:
        return ts[:6]  # YYYYMM...

    months_seen = sorted({to_year_month(ts) for ts in timestamps})
    first_y, first_m = int(months_seen[0][:4]), int(months_seen[0][4:6])
    last_y, last_m = int(months_seen[-1][:4]), int(months_seen[-1][4:6])

    all_months = []
    y, m = first_y, first_m
    while (y, m) <= (last_y, last_m):
        all_months.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1

    gap_months = [mo for mo in all_months if mo not in months_seen]

    return {
        "count": len(timestamps),
        "first_seen": timestamps[0],
        "last_seen": timestamps[-1],
        "distinct_months": len(months_seen),
        "gap_months": gap_months,
    }


async def fetch_cdx_urls(domain: str, fetch_text: FetchText, limit: int = 10000) -> list[dict]:
    """Enumerate every archived URL for a domain. Returns [] (not an
    exception) if the query fails — recorded by the caller, not swallowed.
    """
    url = build_cdx_url(domain, limit=limit)
    text = await fetch_text(url)
    if text is None:
        return []
    return parse_cdx_json(text)
