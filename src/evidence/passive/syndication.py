"""Does this site's inventory also appear on an aggregator we already read?

Many regional brokerages syndicate to Land.com, LandFlip, or an auction
platform. If a hard site syndicates in full to an easy one, the optimal
route to its inventory may be "get it from the aggregator instead" — this
could mean a third of the regional sites (Wave 3) never need their own
scraper. Detection here is a pure footer/badge scan; a real cross-check
(does the aggregator actually carry the same listings) is future work, not
in scope for this no-network pass.
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

KNOWN_AGGREGATORS = [
    "land.com",
    "landwatch.com",
    "landsofamerica.com",
    "landandfarm.com",
    "landflip.com",
    "farmflip.com",
    "ranchflip.com",
    "auctiontime.com",
    "hibid.com",
    "proxibid.com",
    "auctionzip.com",
]


def find_aggregator_badges(html: str, own_domain: str) -> list[dict]:
    """Scan every link on the page for a reference to a known aggregator
    domain (excluding the site's own domain, in case it IS the aggregator).
    """
    tree = HTMLParser(html or "")
    own = own_domain.lower().removeprefix("www.")
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").lower()
        text = (a.text() or "").strip()
        for aggregator in KNOWN_AGGREGATORS:
            if aggregator == own or own.endswith(aggregator) or aggregator.endswith(own):
                continue
            if aggregator in href:
                key = (aggregator, href)
                if key not in seen:
                    seen.add(key)
                    found.append({"aggregator": aggregator, "href": href, "label": text})
    return found


def build_syndication_report(badges: list[dict]) -> dict:
    aggregators = sorted({b["aggregator"] for b in badges})
    return {
        "target_aggregators": aggregators,
        "evidence": badges,
        "verdict": "found" if aggregators else "none found",
    }
