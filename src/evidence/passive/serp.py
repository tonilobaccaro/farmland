"""Search-engine reconnaissance: sample indexed URL shapes and titles via
`site:<domain>` queries. Optional, rate-limited, and OFF by default — search
APIs need keys, and scraping a search engine's own HTML results page as a
substitute is explicitly out of scope (that's exactly the kind of workaround
this project forbids). If no backend is configured, callers get a clean,
explicit skip record rather than an empty/missing result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class SerpBackend(Protocol):
    """Implement this against a real search API (e.g. Bing Web Search,
    SerpAPI, Google Programmable Search) to enable this module. None of those
    integrations ship here — bring your own key.
    """

    async def site_search(self, domain: str, api_key: str) -> list[dict]: ...


BackendFactory = Callable[[], SerpBackend]

_REGISTRY: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    _REGISTRY[name] = factory


async def site_search(domain: str, backend_name: str | None, api_key: str | None) -> dict:
    """Returns a result dict either way: {"skipped": True, "reason": ...} when
    no backend/key is configured, or {"skipped": False, "results": [...]}.
    """
    if not backend_name or not api_key:
        return {"skipped": True, "reason": "no SERP backend configured", "results": []}

    factory = _REGISTRY.get(backend_name)
    if factory is None:
        return {"skipped": True, "reason": f"unknown SERP backend {backend_name!r}", "results": []}

    backend = factory()
    try:
        results = await backend.site_search(domain, api_key)
    except Exception as exc:  # noqa: BLE001 - a SERP failure is a recorded finding, not a crash
        return {"skipped": True, "reason": f"{type(exc).__name__}: {exc}", "results": []}

    return {"skipped": False, "reason": None, "results": results}
