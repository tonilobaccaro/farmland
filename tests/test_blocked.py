from __future__ import annotations

from evidence.blocked import build_blocked_report


def test_fully_open_site():
    by_page_class = {
        "home": {"reachable": True, "tier": "L0", "status": 200},
        "search": {"reachable": True, "tier": "L1", "status": 200},
        "detail": {"reachable": True, "tier": "L0", "status": 200},
    }
    report = build_blocked_report(by_page_class)
    assert report["overall"] == "open"
    assert report["implication"] is None
    assert set(report["still_accessible"]) == {"home", "search", "detail"}


def test_fully_blocked_site():
    by_page_class = {
        "home": {"reachable": False, "tier": None, "status": 403},
    }
    report = build_blocked_report(by_page_class)
    assert report["overall"] == "blocked"
    assert report["still_accessible"] == []
    assert report["implication"] is None


def test_partial_block_search_challenged_detail_open():
    by_page_class = {
        "home": {"reachable": True, "tier": "L1", "status": 200},
        "search": {
            "reachable": False,
            "tier": None,
            "status": 403,
            "vendor": "Cloudflare",
            "signal": "cf-mitigated: challenge",
        },
        "detail": {"reachable": True, "tier": "L2", "status": 200},
    }
    report = build_blocked_report(by_page_class, extra_still_accessible=["sitemap-listings-1.xml"])
    assert report["overall"] == "partial"
    assert "search" not in report["still_accessible"]
    assert "detail" in report["still_accessible"]
    assert "sitemap-listings-1.xml" in report["still_accessible"]
    assert report["implication"] is not None
    assert "search" in report["implication"]


def test_no_page_classes_attempted_is_blocked():
    report = build_blocked_report({})
    assert report["overall"] == "blocked"
