"""Builds 00_meta/blocked_report.json — a status record, never an error log.

Blocking is almost never all-or-nothing; the partial cases are the most
useful thing this produces. See "What success means" in
docs/build-prompts.md.
"""

from __future__ import annotations


def build_blocked_report(
    by_page_class: dict[str, dict],
    extra_still_accessible: list[str] | None = None,
) -> dict:
    """
    by_page_class: {"home": {"reachable": bool, "tier": str|None, "status": int|None,
                              "vendor": str|None, "signal": str|None,
                              "highest_tier_tried": str}, ...}
    """
    reachable_flags = [v.get("reachable", False) for v in by_page_class.values()]

    if not by_page_class:
        overall = "blocked"
    elif all(reachable_flags):
        overall = "open"
    elif not any(reachable_flags):
        overall = "blocked"
    else:
        overall = "partial"

    still_accessible = [k for k, v in by_page_class.items() if v.get("reachable")]
    if extra_still_accessible:
        for item in extra_still_accessible:
            if item not in still_accessible:
                still_accessible.append(item)

    implication = _implication(overall, by_page_class, still_accessible)

    return {
        "overall": overall,
        "by_page_class": by_page_class,
        "still_accessible": still_accessible,
        "implication": implication,
    }


def _implication(overall: str, by_page_class: dict[str, dict], still_accessible: list[str]) -> str | None:
    if overall == "open":
        return None
    if overall == "blocked":
        return None  # nothing reachable at all; nothing to infer a route from

    blocked_classes = [k for k, v in by_page_class.items() if not v.get("reachable")]
    open_classes = [k for k in by_page_class if by_page_class[k].get("reachable")]

    if "search" in blocked_classes and "detail" in open_classes:
        extra = " and sitemap" if any("sitemap" in s for s in still_accessible) else ""
        return f"search is blocked; enumerate via detail-page URLs{extra} and skip the search UI entirely"
    if "search" in blocked_classes and any("sitemap" in s for s in still_accessible):
        return "search is blocked; enumerate via sitemap instead of the search UI"
    if open_classes:
        return f"{', '.join(open_classes)} reachable; {', '.join(blocked_classes)} blocked"
    return None
