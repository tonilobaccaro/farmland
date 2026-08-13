from __future__ import annotations

from pathlib import Path

import pytest

from evidence.artifacts import ArtifactWriter
from evidence.fetch import Fetcher, _RawResponse, classify_blocked_reason, looks_like_challenge
from evidence.models import FetchTier
from evidence.politeness import RateLimiter, RequestBudget, RobotsGate

# ---------------------------------------------------------------------------
# looks_like_challenge / classify_blocked_reason (pure)
# ---------------------------------------------------------------------------


def test_looks_like_challenge_cloudflare_title():
    is_challenge, reason = looks_like_challenge("<title>Just a moment...</title>", {}, 200)
    assert is_challenge
    assert reason == "cloudflare_interstitial_title"


def test_looks_like_challenge_cf_mitigated_header():
    is_challenge, reason = looks_like_challenge("<html>real content here, plenty of it.</html>" * 20, {"cf-mitigated": "challenge"}, 200)
    assert is_challenge
    assert reason == "cf_mitigated_header"


def test_looks_like_challenge_suspiciously_small_body():
    is_challenge, reason = looks_like_challenge("ok", {}, 200)
    assert is_challenge
    assert reason == "suspiciously_small_200_body"


def test_looks_like_challenge_missing_structure():
    body = "x" * 500  # long enough to dodge the small-body check, no <html>/<body>
    is_challenge, reason = looks_like_challenge(body, {}, 200)
    assert is_challenge
    assert reason == "missing_page_structure"


def test_looks_like_challenge_real_page_passes():
    body = "<html><body>" + ("<p>Farmland for sale in Iowa. 160 acres.</p>" * 20) + "</body></html>"
    is_challenge, reason = looks_like_challenge(body, {"content-type": "text/html"}, 200)
    assert not is_challenge
    assert reason is None


def test_classify_blocked_reason_precedence():
    assert classify_blocked_reason(200, True, None) == "waf_challenge"
    assert classify_blocked_reason(403, False, None) == "403"
    assert classify_blocked_reason(429, False, None) == "429"
    assert classify_blocked_reason(200, False, None) is None
    assert classify_blocked_reason(403, False, "ConnectError: boom") is None


# ---------------------------------------------------------------------------
# Fetcher.escalate — network calls mocked via monkeypatched _request
# ---------------------------------------------------------------------------


def _make_fetcher(tmp_path: Path) -> Fetcher:
    artifacts = ArtifactWriter("some-site", tmp_path)
    return Fetcher(
        user_agent="TestBot/0.1 (+https://example.com/bot)",
        rate_limiter=RateLimiter(min_delay_s=0, max_delay_s=0),
        budget=RequestBudget(limit=60),
        robots=RobotsGate(None, "TestBot/0.1", respect_robots=True),
        artifacts=artifacts,
        timeout_s=5.0,
    )


@pytest.mark.asyncio
async def test_escalate_stops_at_first_real_content(tmp_path: Path):
    fetcher = _make_fetcher(tmp_path)

    responses = {
        FetchTier.L0: (None, "TimeoutError: boom"),
        FetchTier.L1: (
            _RawResponse(
                status=200,
                headers={"content-type": "text/html"},
                set_cookies=[],
                body=b"<html><body>" + b"real content " * 50 + b"</body></html>",
                final_url="https://example.com/",
                http_version="HTTP/2",
            ),
            None,
        ),
    }

    async def fake_request(url, tier):
        return responses[tier]

    fetcher._request = fake_request  # type: ignore[method-assign]

    result, tier = await fetcher.escalate("https://example.com/", max_tier=FetchTier.L2, body_rel="home.html")
    assert tier == FetchTier.L1
    assert result.status == 200
    assert result.body_path == "home.html"
    assert (tmp_path / "some-site" / "home.html").exists()


@pytest.mark.asyncio
async def test_escalate_returns_blocked_when_every_tier_challenges(tmp_path: Path):
    fetcher = _make_fetcher(tmp_path)

    challenge_body = b"<title>Just a moment...</title>"

    async def fake_request(url, tier):
        return (
            _RawResponse(
                status=200,
                headers={"cf-mitigated": "challenge"},
                set_cookies=["__cf_bm=abc; Path=/"],
                body=challenge_body,
                final_url=url,
                http_version="HTTP/2",
            ),
            None,
        )

    fetcher._request = fake_request  # type: ignore[method-assign]

    result, tier = await fetcher.escalate("https://example.com/search", max_tier=FetchTier.L2, body_rel="search.html")
    assert tier == FetchTier.BLOCKED
    assert result.blocked_reason == "waf_challenge"
    # the last attempt's (interstitial) body is still saved as evidence
    assert result.body_path == "search.html"
    assert (tmp_path / "some-site" / "search.html").read_bytes() == challenge_body


@pytest.mark.asyncio
async def test_escalate_respects_robots_disallow(tmp_path: Path):
    artifacts = ArtifactWriter("some-site", tmp_path)
    fetcher = Fetcher(
        user_agent="TestBot/0.1",
        rate_limiter=RateLimiter(0, 0),
        budget=RequestBudget(60),
        robots=RobotsGate("User-agent: *\nDisallow: /private/\n", "TestBot/0.1", respect_robots=True),
        artifacts=artifacts,
        timeout_s=5.0,
    )
    result, tier = await fetcher.escalate("https://example.com/private/x", max_tier=FetchTier.L2)
    assert tier == FetchTier.BLOCKED
    assert result.blocked_reason == "robots"
    assert result.error == "robots_disallowed"


@pytest.mark.asyncio
async def test_escalate_stops_when_budget_exhausted(tmp_path: Path):
    fetcher = _make_fetcher(tmp_path)
    fetcher.budget = RequestBudget(limit=0)
    result, tier = await fetcher.escalate("https://example.com/", max_tier=FetchTier.L2)
    assert tier == FetchTier.BLOCKED
    assert result.error is not None and "budget" in result.error
