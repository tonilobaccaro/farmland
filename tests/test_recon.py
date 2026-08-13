from __future__ import annotations

from evidence.recon.legal import find_legal_links
from evidence.recon.robots import extract_disallow_paths, parse_robots
from evidence.recon.sitemap import parse_sitemap_xml, url_path_template_frequency, walk_sitemaps

# ---------------------------------------------------------------------------
# robots.py
# ---------------------------------------------------------------------------

SAMPLE_ROBOTS = """\
User-agent: *
Disallow: /api/internal/
Disallow: /admin/
Crawl-delay: 5
Sitemap: https://example.com/sitemap-index.xml
Sitemap: https://example.com/sitemap-listings.xml

User-agent: FarmlandEvidenceBot
Crawl-delay: 3
Disallow: /search/
"""


def test_extract_disallow_paths_dedupes_and_preserves_order():
    paths = extract_disallow_paths(SAMPLE_ROBOTS)
    assert paths == ["/api/internal/", "/admin/", "/search/"]


def test_parse_robots_extracts_sitemaps_and_crawl_delays():
    parsed = parse_robots(SAMPLE_ROBOTS, "FarmlandEvidenceBot")
    assert set(parsed["sitemaps"]) == {
        "https://example.com/sitemap-index.xml",
        "https://example.com/sitemap-listings.xml",
    }
    assert parsed["crawl_delay_for_us"] == 3
    assert parsed["crawl_delay_for_star"] == 5
    assert "/api/internal/" in parsed["discovered_path_candidates"]


def test_parse_robots_handles_empty_input():
    parsed = parse_robots("", "SomeBot")
    assert parsed["sitemaps"] == []
    assert parsed["discovered_path_candidates"] == []


# ---------------------------------------------------------------------------
# sitemap.py
# ---------------------------------------------------------------------------

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-listings-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-listings-2.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_URLSET_1_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/land/iowa/story-county/123</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://example.com/land/iowa/story-county/456</loc><lastmod>2024-01-01</lastmod></url>
</urlset>
"""

SITEMAP_URLSET_2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/land/illinois/adams-county/789</loc><lastmod>2024-06-15</lastmod></url>
</urlset>
"""


def test_parse_sitemap_xml_index():
    parsed = parse_sitemap_xml(SITEMAP_INDEX_XML)
    assert parsed["kind"] == "sitemapindex"
    assert len(parsed["sitemaps"]) == 2


def test_parse_sitemap_xml_urlset():
    parsed = parse_sitemap_xml(SITEMAP_URLSET_1_XML)
    assert parsed["kind"] == "urlset"
    assert len(parsed["urls"]) == 2
    assert parsed["urls"][0]["lastmod"] == "2024-01-01"


def test_parse_sitemap_xml_malformed():
    parsed = parse_sitemap_xml("<not valid xml")
    assert parsed["kind"] == "unparseable"
    assert "error" in parsed


def test_url_path_template_frequency_collapses_ids():
    counts = url_path_template_frequency(
        ["https://example.com/land/iowa/story-county/123", "https://example.com/land/iowa/story-county/456"]
    )
    assert counts == {"/land/iowa/story-county/{id}": 2}


async def test_walk_sitemaps_recurses_and_aggregates():
    pages = {
        "https://example.com/sitemap-index.xml": SITEMAP_INDEX_XML,
        "https://example.com/sitemap-listings-1.xml": SITEMAP_URLSET_1_XML,
        "https://example.com/sitemap-listings-2.xml": SITEMAP_URLSET_2_XML,
    }

    async def fake_fetch_text(url: str) -> str | None:
        return pages.get(url)

    result = await walk_sitemaps(["https://example.com/sitemap-index.xml"], fake_fetch_text)
    assert result["total_urls"] == 3
    assert result["total_sitemap_files"] == 3
    assert result["lastmod_all_identical"] is False
    assert result["capped_by_files"] is False
    assert result["capped_by_urls"] is False


async def test_walk_sitemaps_records_fetch_failure_without_raising():
    async def always_none(url: str) -> str | None:
        return None

    result = await walk_sitemaps(["https://example.com/sitemap.xml"], always_none)
    assert result["tree"]["https://example.com/sitemap.xml"]["error"] == "fetch_failed"
    assert result["total_urls"] == 0


async def test_walk_sitemaps_respects_max_files_cap():
    calls = {"n": 0}

    async def infinite_index(url: str) -> str | None:
        calls["n"] += 1
        # every sitemap points to one more sitemap, forever
        return f"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/next-{calls['n']}.xml</loc></sitemap>
        </sitemapindex>"""

    result = await walk_sitemaps(["https://example.com/seed.xml"], infinite_index, max_files=5, max_depth=10)
    assert result["total_sitemap_files"] == 5
    assert result["capped_by_files"] is True


# ---------------------------------------------------------------------------
# legal.py
# ---------------------------------------------------------------------------

FOOTER_HTML = """
<html><body>
<header><a href="/land/iowa">Iowa</a></header>
<main><a href="/listing/123">160 acres in Story County</a></main>
<footer>
  <a href="/terms-of-service">Terms of Service</a>
  <a href="/privacy-policy">Privacy Policy</a>
  <a href="/about">About Us</a>
  <a href="https://example.com/legal/copyright">&copy; 2024</a>
</footer>
</body></html>
"""


def test_find_legal_links_matches_keywords_in_text_and_href():
    links = find_legal_links(FOOTER_HTML, "https://example.com/")
    urls = {link["url"] for link in links}
    assert "https://example.com/terms-of-service" in urls
    assert "https://example.com/privacy-policy" in urls
    assert "https://example.com/legal/copyright" in urls
    assert "https://example.com/about" not in urls
    assert "https://example.com/listing/123" not in urls


def test_find_legal_links_dedupes():
    html = '<a href="/terms">Terms</a><a href="/terms">Terms and Conditions</a>'
    links = find_legal_links(html, "https://example.com/")
    assert len(links) == 1


def test_find_legal_links_empty_on_plain_page():
    assert find_legal_links("<html><body>no footer here</body></html>", "https://example.com/") == []
