"""Probe a fixed list of well-known paths. Cheap, standardized, sometimes the
only route into a site's structure or contact info.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from evidence.fetch import Fetcher
from evidence.models import FetchTier

WELLKNOWN_PATHS = [
    "/security.txt",
    "/.well-known/security.txt",
    "/humans.txt",
    "/ads.txt",
    "/sitemap.xml",
    "/robots.txt",
    "/openapi.json",
    "/swagger.json",
    "/api/docs",
]

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify_path(path: str) -> str:
    return _SLUG_RE.sub("_", path.strip("/")) or "root"


async def probe_wellknown(base_url: str, fetcher: Fetcher, artifact_dir: str = "01_policy/wellknown") -> dict:
    """Fetch every WELLKNOWN_PATHS entry at L0 and record found/not-found for each."""
    results: dict[str, dict] = {}
    for path in WELLKNOWN_PATHS:
        url = urljoin(base_url, path)
        found = False
        body_rel = f"{artifact_dir}/{slugify_path(path)}.txt"
        result = await fetcher.fetch(url, FetchTier.L0, body_rel=body_rel)
        found = result.status is not None and result.status < 400 and result.body_bytes > 0
        results[path] = {
            "url": url,
            "found": found,
            "status": result.status,
            "body_path": result.body_path if found else None,
            "body_bytes": result.body_bytes,
            "error": result.error,
        }
    return results
