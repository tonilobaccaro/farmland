"""Find ToS/Terms/Legal/Copyright links by scanning the page for those words.
Captures, does not interpret — per Part 7 of the plan, saving the legal
picture is in scope; deciding what it means is a human decision.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

KEYWORDS = ["terms", "privacy", "legal", "copyright"]
_KEYWORD_RE = re.compile(r"\b(" + "|".join(KEYWORDS) + r")\b", re.IGNORECASE)


def find_legal_links(html: str, base_url: str) -> list[dict]:
    """Every anchor whose text or href mentions terms/privacy/legal/copyright,
    resolved to absolute URLs and deduped.
    """
    tree = HTMLParser(html or "")
    seen: set[str] = set()
    out: list[dict] = []

    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        text = (a.text() or "").strip()
        haystack = f"{text} {href}"
        m = _KEYWORD_RE.search(haystack)
        if not m:
            continue
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append({"label": text, "url": abs_url, "matched_keyword": m.group(1).lower()})

    return out
