"""Certificate transparency via crt.sh: the cheapest way to find api./idx./
search./data./mobile./staging. hosts, which frequently sit outside the WAF
that protects www. Resolution only in this module — per Prompt 1b, any
subdomain that resolves becomes a candidate for p2_static, which applies the
normal budget and robots checks; this module must never itself probe with HTTP.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable

FetchText = Callable[[str], Awaitable[str | None]]
ResolveFn = Callable[[str], Awaitable[bool]]

CRTSH_BASE = "https://crt.sh/"


def build_crtsh_url(domain: str) -> str:
    return f"{CRTSH_BASE}?q=%25.{domain}&output=json"


def parse_crtsh_json(text: str) -> list[dict]:
    try:
        rows = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def extract_subdomains(rows: list[dict], apex_domain: str) -> dict[str, str]:
    """Every distinct hostname seen in `name_value` (which may hold several
    newline-separated SANs per certificate) that belongs to apex_domain,
    mapped to the earliest `not_before` date observed for it.
    """
    apex = apex_domain.lower().lstrip(".")
    first_seen: dict[str, str] = {}

    for row in rows:
        name_value = row.get("name_value") or ""
        not_before = row.get("not_before") or ""
        for raw_name in name_value.split("\n"):
            name = raw_name.strip().lower()
            if not name:
                continue
            name = name.removeprefix("*.")
            if name != apex and not name.endswith("." + apex):
                continue
            if name not in first_seen or (not_before and not_before < first_seen[name]):
                first_seen[name] = not_before

    return first_seen


async def default_resolve(hostname: str) -> bool:
    try:
        await __import__("asyncio").get_event_loop().run_in_executor(
            None, socket.getaddrinfo, hostname, None
        )
        return True
    except OSError:
        return False


async def resolve_subdomains(subdomains: list[str], resolve_fn: ResolveFn = default_resolve) -> dict[str, bool]:
    """DNS resolution only — no HTTP probing. Which subdomains currently
    resolve is recon value on its own (dead vs. live infrastructure).
    """
    results: dict[str, bool] = {}
    for name in subdomains:
        results[name] = await resolve_fn(name)
    return results


async def fetch_subdomains(domain: str, fetch_text: FetchText) -> list[dict]:
    url = build_crtsh_url(domain)
    text = await fetch_text(url)
    if text is None:
        return []
    return parse_crtsh_json(text)
