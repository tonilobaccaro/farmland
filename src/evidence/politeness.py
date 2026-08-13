"""Rate limiting, request budgeting, and robots.txt policy.

Guardrails from Part 7 of docs/evidence-gathering-plan.md, encoded here rather
than left to discipline: 1 concurrent request per host, 2-5s jittered delay,
exponential backoff on 429/503, honor Retry-After, hard per-site request cap.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse


class BudgetExhausted(Exception):
    """Raised by RequestBudget.consume() once the per-site request cap is hit."""


@dataclass
class RequestBudget:
    """Hard cap on requests made to one site's origin(s) during a run."""

    limit: int = 60
    used: int = 0

    def consume(self, n: int = 1) -> None:
        if self.used + n > self.limit:
            raise BudgetExhausted(
                f"request budget exhausted: {self.used}/{self.limit} used, "
                f"{n} more requested"
            )
        self.used += n

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass
class RateLimiter:
    """Per-host jittered delay, honoring Retry-After and backing off on 429/503."""

    min_delay_s: float = 2.0
    max_delay_s: float = 5.0
    _last_request_at: dict[str, float] = field(default_factory=dict)
    _backoff_until: dict[str, float] = field(default_factory=dict)

    def _host(self, url: str) -> str:
        return urlparse(url).netloc

    async def wait(self, url: str) -> None:
        host = self._host(url)
        now = time.monotonic()

        backoff_until = self._backoff_until.get(host, 0.0)
        if backoff_until > now:
            await asyncio.sleep(backoff_until - now)
            now = time.monotonic()

        last = self._last_request_at.get(host)
        if last is not None:
            jitter = random.uniform(self.min_delay_s, self.max_delay_s)
            elapsed = now - last
            remaining = jitter - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

        self._last_request_at[host] = time.monotonic()

    def note_response(self, url: str, status: int, retry_after_s: float | None = None) -> None:
        """Record a response so the next wait() for this host backs off appropriately."""
        host = self._host(url)
        if retry_after_s is not None:
            self._backoff_until[host] = time.monotonic() + retry_after_s
            return
        if status in (429, 503):
            prior = self._backoff_until.get(host, 0.0)
            base = max(self.max_delay_s, 1.0)
            # exponential backoff: doubles each consecutive 429/503, capped at 5 minutes
            current_extra = max(0.0, prior - time.monotonic())
            next_extra = min(300.0, max(base, current_extra * 2))
            self._backoff_until[host] = time.monotonic() + next_extra


@dataclass
class RobotsConflict:
    url: str
    reason: str


class RobotsGate:
    """Wraps protego. When respect_robots is True and a URL is disallowed, the
    conflict is recorded (not silently skipped) — the report must show what we
    could not look at.
    """

    def __init__(self, robots_txt: str | None, user_agent: str, respect_robots: bool = True):
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self.conflicts: list[RobotsConflict] = []
        self._protego = None
        if robots_txt:
            from protego import Protego

            self._protego = Protego.parse(robots_txt)

    def reload(self, robots_txt: str | None) -> None:
        """Rebuild policy from freshly fetched robots.txt.

        Bootstrapping: a phase running early in a fresh run has no
        01_policy/robots.txt on disk yet when PhaseContext is built, so the
        gate starts permissive. Once p1_recon fetches and parses robots.txt,
        it calls this so every later fetch in the same run — by p1_recon
        itself or any phase after it — is actually gated by real policy.
        """
        if robots_txt:
            from protego import Protego

            self._protego = Protego.parse(robots_txt)

    def allowed(self, url: str) -> bool:
        if self._protego is None:
            return True
        is_allowed = self._protego.can_fetch(url, self.user_agent)
        if not is_allowed:
            self.conflicts.append(RobotsConflict(url=url, reason="disallowed_by_robots"))
            if self.respect_robots:
                return False
        return True

    def crawl_delay(self) -> float | None:
        if self._protego is None:
            return None
        return self._protego.crawl_delay(self.user_agent)

    def sitemaps(self) -> list[str]:
        if self._protego is None:
            return []
        return list(self._protego.sitemaps)
