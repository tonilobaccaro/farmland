"""Recursive sitemap-index walker with hard caps.

The walk itself (`walk_sitemaps`) takes an injected `fetch_text` async
function rather than doing HTTP directly, so the traversal, capping, and
lastmod/template analysis logic can be unit-tested against synthetic sitemap
XML without touching a network.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from xml.etree import ElementTree as ET

FetchText = Callable[[str], Awaitable[str | None]]

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_FILES = 50
DEFAULT_MAX_URLS = 50_000


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_sitemap_xml(xml_text: str) -> dict:
    """Parse one sitemap document: either a <sitemapindex> (nested sitemaps) or
    a <urlset> (leaf URLs with optional lastmod). Malformed XML is reported,
    not raised.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return {"kind": "unparseable", "sitemaps": [], "urls": [], "error": str(exc)}

    root_tag = _local_tag(root.tag)
    sitemaps: list[str] = []
    urls: list[dict] = []

    if root_tag == "sitemapindex":
        for sitemap_el in root:
            if _local_tag(sitemap_el.tag) != "sitemap":
                continue
            loc = None
            for child in sitemap_el:
                if _local_tag(child.tag) == "loc" and child.text:
                    loc = child.text.strip()
            if loc:
                sitemaps.append(loc)
        return {"kind": "sitemapindex", "sitemaps": sitemaps, "urls": []}

    if root_tag == "urlset":
        for url_el in root:
            if _local_tag(url_el.tag) != "url":
                continue
            loc, lastmod = None, None
            for child in url_el:
                ctag = _local_tag(child.tag)
                if ctag == "loc" and child.text:
                    loc = child.text.strip()
                elif ctag == "lastmod" and child.text:
                    lastmod = child.text.strip()
            if loc:
                urls.append({"loc": loc, "lastmod": lastmod})
        return {"kind": "urlset", "sitemaps": [], "urls": urls}

    return {"kind": "unknown", "sitemaps": [], "urls": []}


_SEGMENT_NUMERIC = re.compile(r"^\d+$")
_SEGMENT_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _templatize_path(url: str) -> str:
    """Collapse numeric/UUID path segments to placeholders so /listing/123 and
    /listing/456 count as one template.
    """
    path = url.split("://", 1)[-1].split("/", 1)
    path = path[1] if len(path) > 1 else ""
    segments = [s for s in path.split("/") if s]
    out = []
    for seg in segments:
        if _SEGMENT_UUID.match(seg):
            out.append("{uuid}")
        elif _SEGMENT_NUMERIC.match(seg):
            out.append("{id}")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def url_path_template_frequency(urls: list[str]) -> dict[str, int]:
    counter = Counter(_templatize_path(u) for u in urls)
    return dict(counter.most_common())


async def walk_sitemaps(
    seed_urls: list[str],
    fetch_text: FetchText,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_files: int = DEFAULT_MAX_FILES,
    max_urls: int = DEFAULT_MAX_URLS,
) -> dict:
    """Recursively walk a sitemap index tree starting from seed_urls.

    `fetch_text(url) -> str | None` is caller-injected so this function has no
    network dependency of its own; it returns None for a URL that failed to
    fetch, which is recorded (not silently dropped).
    """
    visited: set[str] = set()
    tree: dict[str, dict] = {}
    all_urls: list[str] = []
    lastmods: list[str] = []
    queue: list[tuple[str, int]] = [(u, 0) for u in seed_urls]
    files_fetched = 0
    capped_by_files = False
    capped_by_urls = False

    while queue and files_fetched < max_files:
        url, depth = queue.pop(0)
        if url in visited or depth > max_depth:
            continue
        visited.add(url)

        text = await fetch_text(url)
        files_fetched += 1
        if text is None:
            tree[url] = {"depth": depth, "error": "fetch_failed"}
            continue

        parsed = parse_sitemap_xml(text)
        tree[url] = {
            "depth": depth,
            "kind": parsed["kind"],
            "sitemap_count": len(parsed["sitemaps"]),
            "url_count": len(parsed["urls"]),
        }
        if parsed["kind"] == "unparseable":
            tree[url]["error"] = parsed.get("error")

        for child_sitemap in parsed["sitemaps"]:
            if depth + 1 <= max_depth:
                queue.append((child_sitemap, depth + 1))

        for entry in parsed["urls"]:
            if len(all_urls) >= max_urls:
                capped_by_urls = True
                break
            all_urls.append(entry["loc"])
            if entry.get("lastmod"):
                lastmods.append(entry["lastmod"])

    if queue and files_fetched >= max_files:
        capped_by_files = True

    distinct_lastmods = set(lastmods)
    return {
        "tree": tree,
        "total_sitemap_files": files_fetched,
        "total_urls": len(all_urls),
        "lastmod_sample": lastmods[:50],
        "lastmod_distinct_count": len(distinct_lastmods),
        "lastmod_all_identical": (len(distinct_lastmods) == 1) if lastmods else None,
        "url_path_template_counts": url_path_template_frequency(all_urls),
        "capped_by_files": capped_by_files,
        "capped_by_urls": capped_by_urls,
    }
