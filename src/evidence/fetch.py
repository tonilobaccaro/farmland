"""The fetch escalation ladder: L0 (httpx, honest UA) -> L1 (httpx, browser
headers) -> L2 (curl_cffi, TLS impersonation). Stops at L2 here; L3/L4
(Playwright) are added in Prompt 2. Every request goes through RateLimiter,
RequestBudget and RobotsGate — nothing in this module calls a client directly.

"Real content" is not just status 200: a WAF interstitial routinely returns
200. `looks_like_challenge` is the function that catches that.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import httpx

from evidence.artifacts import ArtifactWriter
from evidence.models import FetchResult, FetchTier
from evidence.politeness import BudgetExhausted, RateLimiter, RequestBudget, RobotsGate

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Full Chrome navigation header set, in the order a real Chrome request sends
# them. Used at L1/L2 for header-realism diagnostics on allowed paths — this
# is not evasion (see the Guardrails section of CLAUDE.md): the point is to
# tell "blocks anything non-browser" apart from "blocks bots specifically."
CHROME_HEADERS: dict[str, str] = {
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": CHROME_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
}

# Tiers this module implements, low to high cost. L3/L4 arrive in Prompt 2.
ESCALATION_ORDER = [FetchTier.L0, FetchTier.L1, FetchTier.L2]

CHALLENGE_MARKERS: list[tuple[str, str]] = [
    ("just a moment", "cloudflare_interstitial_title"),
    ("checking your browser before accessing", "cloudflare_interstitial_text"),
    ("attention required! | cloudflare", "cloudflare_attention_required"),
    ("please verify you are a human", "perimeterx_human_check"),
    ("_pxcaptcha", "perimeterx_captcha"),
    ("captcha-delivery.com", "datadome_captcha"),
    ("are you a robot", "generic_robot_check"),
    ("access denied", "generic_access_denied"),
    ("request unsuccessful. incapsula", "imperva_incident"),
]


def looks_like_challenge(
    body: str, headers: dict[str, str], status: int | None = None
) -> tuple[bool, str | None]:
    """Classify whether a response is a WAF interstitial rather than real content.

    Checks challenge markers in the body, the cf-mitigated header, a
    suspiciously small 200 body, and a 200 body missing basic HTML structure.
    Returns (is_challenge, reason_code).
    """
    text = body or ""
    lower = text.lower()
    header_l = {k.lower(): v for k, v in (headers or {}).items()}

    for marker, code in CHALLENGE_MARKERS:
        if marker in lower:
            return True, code

    if "challenge" in header_l.get("cf-mitigated", "").lower():
        return True, "cf_mitigated_header"

    if "/cdn-cgi/challenge-platform/" in text:
        return True, "cloudflare_challenge_script"

    stripped_len = len(text.strip())
    if status == 200 and 0 < stripped_len < 300:
        return True, "suspiciously_small_200_body"

    if status == 200 and stripped_len > 0 and "<body" not in lower and "<html" not in lower:
        return True, "missing_page_structure"

    return False, None


def classify_blocked_reason(
    status: int | None, is_challenge: bool, network_error: str | None
) -> str | None:
    if network_error is not None:
        return None  # a transport error isn't a "block" finding, just a failure
    if is_challenge:
        return "waf_challenge"
    if status == 403:
        return "403"
    if status == 429:
        return "429"
    return None


@dataclass
class _RawResponse:
    status: int | None
    headers: dict[str, str]
    set_cookies: list[str]
    body: bytes
    final_url: str
    http_version: str | None
    redirect_chain: list[str] = field(default_factory=list)


class Fetcher:
    """Issues requests for one site through the fetch ladder.

    Every request is gated by RateLimiter (jittered delay + backoff),
    RequestBudget (hard per-site cap) and RobotsGate (respect_robots policy) —
    always in that order, always before any bytes leave the machine.
    """

    def __init__(
        self,
        user_agent: str,
        rate_limiter: RateLimiter,
        budget: RequestBudget,
        robots: RobotsGate,
        artifacts: ArtifactWriter,
        timeout_s: float = 30.0,
    ) -> None:
        self.user_agent = user_agent
        self.rate_limiter = rate_limiter
        self.budget = budget
        self.robots = robots
        self.artifacts = artifacts
        self.timeout_s = timeout_s

    async def _fetch_l0(self, url: str) -> _RawResponse:
        headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        async with httpx.AsyncClient(
            http1=True, http2=False, follow_redirects=True, timeout=self.timeout_s
        ) as client:
            resp = await client.get(url, headers=headers)
        return self._from_httpx(resp)

    async def _fetch_l1(self, url: str) -> _RawResponse:
        async with httpx.AsyncClient(
            http2=True, follow_redirects=True, timeout=self.timeout_s
        ) as client:
            resp = await client.get(url, headers=dict(CHROME_HEADERS))
        return self._from_httpx(resp)

    async def _fetch_l2(self, url: str) -> _RawResponse:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as session:
            resp = await session.get(
                url, impersonate="chrome", timeout=self.timeout_s, allow_redirects=True
            )
        set_cookies = []
        try:
            set_cookies = [f"{k}={v}" for k, v in resp.cookies.items()]
        except Exception:  # noqa: BLE001 - cookie jar shape varies across curl_cffi versions
            set_cookies = []
        history = getattr(resp, "history", None) or []
        return _RawResponse(
            status=resp.status_code,
            headers=dict(resp.headers),
            set_cookies=set_cookies,
            body=resp.content or b"",
            final_url=str(resp.url),
            http_version=str(getattr(resp, "http_version", "") or "") or None,
            redirect_chain=[str(getattr(h, "url", "")) for h in history],
        )

    def _from_httpx(self, resp: httpx.Response) -> _RawResponse:
        try:
            set_cookies = [f"{k}={v}" for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
        except AttributeError:
            raw = resp.headers.get("set-cookie")
            set_cookies = [raw] if raw else []
        return _RawResponse(
            status=resp.status_code,
            headers=dict(resp.headers),
            set_cookies=set_cookies,
            body=resp.content or b"",
            final_url=str(resp.url),
            http_version=resp.http_version,
            redirect_chain=[str(r.url) for r in resp.history],
        )

    async def _request(self, url: str, tier: FetchTier) -> tuple[_RawResponse | None, str | None]:
        """Run one request through politeness gates. Returns (response, error)."""
        if not self.robots.allowed(url):
            return None, "robots_disallowed"
        try:
            self.budget.consume()
        except BudgetExhausted as exc:
            return None, str(exc)

        await self.rate_limiter.wait(url)
        dispatch = {FetchTier.L0: self._fetch_l0, FetchTier.L1: self._fetch_l1, FetchTier.L2: self._fetch_l2}
        try:
            raw = await dispatch[tier](url)
        except Exception as exc:  # noqa: BLE001 - a transport failure is a recorded finding
            return None, f"{type(exc).__name__}: {exc}"

        retry_after = raw.headers.get("retry-after") if raw.headers else None
        retry_after_s = float(retry_after) if retry_after and retry_after.isdigit() else None
        self.rate_limiter.note_response(url, raw.status or 0, retry_after_s)
        return raw, None

    def _to_result(
        self, url: str, tier: FetchTier, raw: _RawResponse | None, error: str | None, elapsed_ms: int, body_rel: str | None
    ) -> FetchResult:
        if raw is None:
            blocked_reason = "robots" if error == "robots_disallowed" else None
            return FetchResult(
                url=url,
                final_url=url,
                status=None,
                tier=tier,
                elapsed_ms=elapsed_ms,
                error=error,
                blocked_reason=blocked_reason,
            )

        body_text = raw.body.decode("utf-8", errors="replace") if raw.body else ""
        is_challenge, _reason = looks_like_challenge(body_text, raw.headers, raw.status)
        blocked_reason = classify_blocked_reason(raw.status, is_challenge, None)

        body_path = None
        if body_rel is not None:
            body_path = self.artifacts.bytes(body_rel, raw.body)

        return FetchResult(
            url=url,
            final_url=raw.final_url,
            status=raw.status,
            tier=tier,
            http_version=raw.http_version,
            headers=raw.headers,
            set_cookies=raw.set_cookies,
            body_path=body_path,
            body_sha256=hashlib.sha256(raw.body).hexdigest() if raw.body else None,
            body_bytes=len(raw.body),
            elapsed_ms=elapsed_ms,
            redirect_chain=raw.redirect_chain,
            error=None,
            blocked_reason=blocked_reason,
        )

    async def fetch(self, url: str, tier: FetchTier, body_rel: str | None = None) -> FetchResult:
        """Fetch one URL at exactly one tier. Body is written via ArtifactWriter
        (never held on the model) when body_rel is given.
        """
        start = time.monotonic()
        raw, error = await self._request(url, tier)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return self._to_result(url, tier, raw, error, elapsed_ms, body_rel)

    async def fetch_text(
        self, url: str, tier: FetchTier, body_rel: str | None = None
    ) -> tuple[FetchResult, str | None]:
        """Like fetch(), but also hands back decoded body text for immediate
        parsing (robots.txt, sitemap XML, homepage HTML for a footer scan) —
        without holding it on the FetchResult model or re-reading it off disk.
        """
        start = time.monotonic()
        raw, error = await self._request(url, tier)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = self._to_result(url, tier, raw, error, elapsed_ms, body_rel)
        text = raw.body.decode("utf-8", errors="replace") if raw and raw.body else None
        return result, text

    async def escalate(
        self, url: str, max_tier: FetchTier = FetchTier.L2, body_rel: str | None = None
    ) -> tuple[FetchResult, FetchTier]:
        """Try tiers in order; stop at the first that returns real (non-challenge)
        content. If every tier is blocked or errors, returns the last attempt's
        FetchResult paired with FetchTier.BLOCKED.
        """
        max_index = ESCALATION_ORDER.index(max_tier)
        tiers = ESCALATION_ORDER[: max_index + 1]

        last_tier = tiers[0]
        last_raw: _RawResponse | None = None
        last_error: str | None = None
        last_elapsed_ms = 0

        for tier in tiers:
            start = time.monotonic()
            raw, error = await self._request(url, tier)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            last_tier, last_raw, last_error, last_elapsed_ms = tier, raw, error, elapsed_ms

            if raw is not None and raw.status is not None and raw.status < 400:
                body_text = raw.body.decode("utf-8", errors="replace") if raw.body else ""
                is_challenge, _reason = looks_like_challenge(body_text, raw.headers, raw.status)
                if not is_challenge:
                    result = self._to_result(url, tier, raw, error, elapsed_ms, body_rel)
                    return result, tier

        # Nothing worked: persist the last attempt's body (often the interstitial
        # itself, which is useful evidence) and report BLOCKED.
        result = self._to_result(url, last_tier, last_raw, last_error, last_elapsed_ms, body_rel)
        return result, FetchTier.BLOCKED
