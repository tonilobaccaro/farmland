from __future__ import annotations

import json

from evidence.passive.archive import (
    archive_coverage,
    build_cdx_url,
    build_snapshot_url,
    fetch_cdx_urls,
    parse_cdx_json,
)
from evidence.passive.commoncrawl import (
    build_cc_cdx_url,
    parse_cc_ndjson,
    parse_collinfo,
    query_recent_crawls,
)
from evidence.passive.ctlogs import (
    build_crtsh_url,
    extract_subdomains,
    fetch_subdomains,
    parse_crtsh_json,
    resolve_subdomains,
)
from evidence.passive.serp import site_search
from evidence.passive.syndication import build_syndication_report, find_aggregator_badges

# ---------------------------------------------------------------------------
# archive.py
# ---------------------------------------------------------------------------

CDX_JSON = json.dumps(
    [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        [
            "com,example)/land/iowa/story-county/123",
            "20230115120000",
            "https://example.com/land/iowa/story-county/123",
            "text/html",
            "200",
            "ABC123",
            "5000",
        ],
        [
            "com,example)/land/iowa/story-county/456",
            "20240301090000",
            "https://example.com/land/iowa/story-county/456",
            "text/html",
            "200",
            "DEF456",
            "5200",
        ],
    ]
)


def test_build_cdx_url():
    url = build_cdx_url("example.com", limit=500)
    assert "url=example.com/*" in url
    assert "limit=500" in url


def test_build_snapshot_url_uses_id_suffix():
    url = build_snapshot_url("20230115120000", "https://example.com/x")
    assert url == "http://web.archive.org/web/20230115120000id_/https://example.com/x"


def test_parse_cdx_json_valid():
    rows = parse_cdx_json(CDX_JSON)
    assert len(rows) == 2
    assert rows[0]["original"] == "https://example.com/land/iowa/story-county/123"
    assert rows[0]["timestamp"] == "20230115120000"


def test_parse_cdx_json_malformed_returns_empty():
    assert parse_cdx_json("not json") == []
    assert parse_cdx_json("[]") == []
    assert parse_cdx_json(json.dumps([["header_only"]])) == []


def test_archive_coverage_empty():
    cov = archive_coverage([])
    assert cov["count"] == 0
    assert cov["gap_months"] == []


def test_archive_coverage_computes_range_and_gaps():
    rows = parse_cdx_json(CDX_JSON)
    cov = archive_coverage(rows)
    assert cov["count"] == 2
    assert cov["first_seen"] == "20230115120000"
    assert cov["last_seen"] == "20240301090000"
    # 2023-01 through 2024-03 is 15 months; only 2 are covered
    assert cov["distinct_months"] == 2
    assert len(cov["gap_months"]) == 13


async def test_fetch_cdx_urls_uses_injected_fetch_text():
    async def fake_fetch(url: str) -> str | None:
        assert "url=example.com/*" in url
        return CDX_JSON

    rows = await fetch_cdx_urls("example.com", fake_fetch)
    assert len(rows) == 2


async def test_fetch_cdx_urls_handles_fetch_failure():
    async def fake_fetch(url: str) -> str | None:
        return None

    rows = await fetch_cdx_urls("example.com", fake_fetch)
    assert rows == []


# ---------------------------------------------------------------------------
# commoncrawl.py
# ---------------------------------------------------------------------------

COLLINFO_JSON = json.dumps(
    [
        {"id": "CC-MAIN-2024-33", "name": "August 2024"},
        {"id": "CC-MAIN-2024-26", "name": "June 2024"},
        {"id": "CC-MAIN-2024-18", "name": "April 2024"},
        {"id": "CC-MAIN-2024-10", "name": "February 2024"},
    ]
)

CC_NDJSON = "\n".join(
    [
        json.dumps({"url": "https://example.com/land/1", "timestamp": "20240801000000"}),
        json.dumps({"url": "https://example.com/land/2", "timestamp": "20240801000000"}),
        "",  # trailing blank line should be ignored
    ]
)


def test_build_cc_cdx_url():
    url = build_cc_cdx_url("CC-MAIN-2024-33", "example.com")
    assert url == "https://index.commoncrawl.org/CC-MAIN-2024-33-index?url=example.com/*&output=json"


def test_parse_collinfo():
    ids = parse_collinfo(COLLINFO_JSON)
    assert ids[0] == "CC-MAIN-2024-33"
    assert len(ids) == 4


def test_parse_cc_ndjson_skips_blank_and_malformed_lines():
    records = parse_cc_ndjson(CC_NDJSON + "\nnot json\n")
    assert len(records) == 2
    assert records[0]["url"] == "https://example.com/land/1"


async def test_query_recent_crawls_aggregates_across_crawl_ids():
    pages = {
        "https://index.commoncrawl.org/collinfo.json": COLLINFO_JSON,
        "https://index.commoncrawl.org/CC-MAIN-2024-33-index?url=example.com/*&output=json": CC_NDJSON,
        "https://index.commoncrawl.org/CC-MAIN-2024-26-index?url=example.com/*&output=json": None,
        "https://index.commoncrawl.org/CC-MAIN-2024-18-index?url=example.com/*&output=json": CC_NDJSON,
    }

    async def fake_fetch(url: str) -> str | None:
        return pages.get(url)

    result = await query_recent_crawls("example.com", fake_fetch, n=3)
    assert result["crawl_ids_queried"] == ["CC-MAIN-2024-33", "CC-MAIN-2024-26", "CC-MAIN-2024-18"]
    assert result["total_urls"] == 4  # 2 + 0 (failed) + 2
    assert result["results"]["CC-MAIN-2024-26"]["error"] == "fetch_failed"


# ---------------------------------------------------------------------------
# ctlogs.py
# ---------------------------------------------------------------------------

CRTSH_JSON = json.dumps(
    [
        {"name_value": "api.example.com", "not_before": "2022-01-01T00:00:00"},
        {"name_value": "www.example.com\nexample.com", "not_before": "2023-05-01T00:00:00"},
        {"name_value": "*.idx.example.com", "not_before": "2021-06-01T00:00:00"},
        {"name_value": "api.example.com", "not_before": "2020-02-01T00:00:00"},  # earlier cert, same host
        {"name_value": "unrelated-domain.net", "not_before": "2022-01-01T00:00:00"},
    ]
)


def test_build_crtsh_url():
    url = build_crtsh_url("example.com")
    assert url == "https://crt.sh/?q=%25.example.com&output=json"


def test_parse_crtsh_json():
    rows = parse_crtsh_json(CRTSH_JSON)
    assert len(rows) == 5


def test_extract_subdomains_dedupes_and_keeps_earliest_date():
    rows = parse_crtsh_json(CRTSH_JSON)
    subs = extract_subdomains(rows, "example.com")
    assert subs["api.example.com"] == "2020-02-01T00:00:00"  # earliest of the two certs
    assert "idx.example.com" in subs  # wildcard prefix stripped
    assert "unrelated-domain.net" not in subs
    assert "example.com" in subs  # apex itself is a valid entry


async def test_fetch_subdomains_uses_injected_fetch_text():
    async def fake_fetch(url: str) -> str | None:
        return CRTSH_JSON

    rows = await fetch_subdomains("example.com", fake_fetch)
    assert len(rows) == 5


async def test_resolve_subdomains_with_injected_resolver():
    async def fake_resolve(hostname: str) -> bool:
        return hostname == "api.example.com"

    result = await resolve_subdomains(["api.example.com", "ghost.example.com"], fake_resolve)
    assert result == {"api.example.com": True, "ghost.example.com": False}


# ---------------------------------------------------------------------------
# serp.py
# ---------------------------------------------------------------------------


async def test_site_search_skips_cleanly_with_no_backend():
    result = await site_search("example.com", backend_name=None, api_key=None)
    assert result["skipped"] is True
    assert result["results"] == []


async def test_site_search_skips_on_unknown_backend():
    result = await site_search("example.com", backend_name="nonexistent", api_key="key")
    assert result["skipped"] is True
    assert "unknown" in result["reason"]


# ---------------------------------------------------------------------------
# syndication.py
# ---------------------------------------------------------------------------

FOOTER_WITH_AGGREGATOR = """
<html><body>
<footer>
  <a href="https://www.landwatch.com/some-brokerage-listings">Also listed on LandWatch</a>
  <a href="/privacy">Privacy</a>
</footer>
</body></html>
"""


def test_find_aggregator_badges_detects_known_aggregator():
    badges = find_aggregator_badges(FOOTER_WITH_AGGREGATOR, "example.com")
    assert len(badges) == 1
    assert badges[0]["aggregator"] == "landwatch.com"


def test_find_aggregator_badges_excludes_own_domain():
    html = '<a href="https://www.landwatch.com/listing/1">See more</a>'
    badges = find_aggregator_badges(html, "landwatch.com")
    assert badges == []


def test_build_syndication_report_none_found():
    report = build_syndication_report([])
    assert report["verdict"] == "none found"
    assert report["target_aggregators"] == []


def test_build_syndication_report_found():
    badges = [{"aggregator": "landwatch.com", "href": "https://landwatch.com/x", "label": "LandWatch"}]
    report = build_syndication_report(badges)
    assert report["verdict"] == "found"
    assert report["target_aggregators"] == ["landwatch.com"]
