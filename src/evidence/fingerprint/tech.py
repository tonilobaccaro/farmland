"""Framework/CMS/search-backend fingerprinting.

Signals come from script srcs, the meta generator tag, response headers,
cookie names, and DOM class-name prefixes — never from a network probe (that's
a separate, budgeted request the calling phase decides whether to make; see
`wp_json_hint` below for the one place that distinction matters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TechRule:
    technology: str
    source: str  # "html" | "script_src" | "header" | "cookie" | "class_prefix"
    pattern: re.Pattern[str]
    confidence: float
    description: str


RULES: list[TechRule] = [
    # --- Next.js ---------------------------------------------------------
    TechRule("Next.js", "html", re.compile(r"__NEXT_DATA__"), 0.95, "__NEXT_DATA__ script tag"),
    TechRule("Next.js", "html", re.compile(r"self\.__next_f\.push"), 0.9, "self.__next_f.push (app router streaming)"),
    TechRule("Next.js", "script_src", re.compile(r"/_next/static/"), 0.8, "/_next/static/ asset path"),
    # --- Nuxt --------------------------------------------------------------
    TechRule("Nuxt", "html", re.compile(r"window\.__NUXT__"), 0.95, "window.__NUXT__ global"),
    TechRule("Nuxt", "script_src", re.compile(r"/_nuxt/"), 0.8, "/_nuxt/ asset path"),
    # --- Remix ---------------------------------------------------------
    TechRule("Remix", "html", re.compile(r"__remixContext"), 0.95, "__remixContext global"),
    TechRule("Remix", "script_src", re.compile(r"/build/_shared/|remix-run"), 0.7, "remix-run/build asset path"),
    # --- SvelteKit -----------------------------------------------------
    TechRule("SvelteKit", "html", re.compile(r"__sveltekit"), 0.9, "__sveltekit global"),
    TechRule("SvelteKit", "script_src", re.compile(r"/_app/immutable/"), 0.8, "/_app/immutable/ asset path"),
    # --- Angular ---------------------------------------------------------
    TechRule("Angular", "html", re.compile(r"\bng-version="), 0.9, "ng-version attribute"),
    TechRule("Angular", "html", re.compile(r"<app-root\b"), 0.6, "<app-root> element"),
    # --- React -------------------------------------------------------------
    TechRule("React", "html", re.compile(r"data-reactroot"), 0.85, "data-reactroot attribute"),
    TechRule("React", "script_src", re.compile(r"react-dom(\.min)?\.js"), 0.6, "react-dom bundle reference"),
    # --- Vue -----------------------------------------------------------
    TechRule("Vue", "html", re.compile(r"\bdata-v-[0-9a-f]{6,}\b"), 0.85, "data-v-* scoped-style attribute"),
    TechRule("Vue", "script_src", re.compile(r"vue(\.runtime)?(\.min)?\.js"), 0.6, "vue.js bundle reference"),
    # --- WordPress -----------------------------------------------------
    TechRule(
        "WordPress",
        "html",
        re.compile(r'<meta\s+name=["\']generator["\']\s+content=["\']WordPress', re.IGNORECASE),
        0.95,
        "generator meta tag",
    ),
    TechRule("WordPress", "script_src", re.compile(r"/wp-content/|/wp-includes/"), 0.85, "wp-content/wp-includes asset path"),
    TechRule("WordPress", "html", re.compile(r"/wp-json/"), 0.6, "/wp-json/ reference in page"),
    # --- Drupal --------------------------------------------------------
    TechRule(
        "Drupal",
        "html",
        re.compile(r'<meta\s+name=["\']generator["\']\s+content=["\']Drupal', re.IGNORECASE),
        0.95,
        "generator meta tag",
    ),
    TechRule("Drupal", "html", re.compile(r"Drupal\.settings|drupal\.js"), 0.7, "Drupal.settings / drupal.js reference"),
    TechRule("Drupal", "script_src", re.compile(r"/sites/default/files/"), 0.6, "/sites/default/files/ asset path"),
    # --- ASP.NET WebForms -----------------------------------------------
    TechRule("ASP.NET WebForms", "html", re.compile(r'name=["\']__VIEWSTATE["\']'), 0.95, "__VIEWSTATE hidden field"),
    TechRule(
        "ASP.NET WebForms",
        "html",
        re.compile(r'name=["\']__EVENTVALIDATION["\']'),
        0.9,
        "__EVENTVALIDATION hidden field",
    ),
    # --- Squarespace -----------------------------------------------------
    TechRule("Squarespace", "script_src", re.compile(r"static1\.squarespace\.com|squarespace-cdn\.com"), 0.85, "squarespace CDN asset"),
    TechRule(
        "Squarespace",
        "html",
        re.compile(r'<meta\s+name=["\']generator["\']\s+content=["\']Squarespace', re.IGNORECASE),
        0.95,
        "generator meta tag",
    ),
    # --- Wix -----------------------------------------------------------
    TechRule("Wix", "script_src", re.compile(r"static\.wixstatic\.com|static\.parastorage\.com"), 0.85, "wix static asset host"),
    TechRule("Wix", "header", re.compile(r"^x-wix-"), 0.8, "X-Wix-* header"),
    # --- Webflow ---------------------------------------------------------
    TechRule("Webflow", "html", re.compile(r"data-wf-site|data-wf-page"), 0.9, "data-wf-site/data-wf-page attribute"),
    TechRule("Webflow", "script_src", re.compile(r"assets\.website-files\.com"), 0.85, "assets.website-files.com asset host"),
    # --- Algolia ---------------------------------------------------------
    TechRule("Algolia", "html", re.compile(r"algoliasearch|algolia\.net"), 0.8, "algoliasearch client / algolia.net host reference"),
    TechRule("Algolia", "script_src", re.compile(r"cdn\.jsdelivr\.net/npm/algoliasearch"), 0.7, "algoliasearch CDN bundle"),
    # --- Elasticsearch ---------------------------------------------------
    TechRule("Elasticsearch", "header", re.compile(r"^x-elastic-product$"), 0.9, "X-elastic-product header"),
    TechRule("Elasticsearch", "html", re.compile(r"elasticsearch", re.IGNORECASE), 0.4, "'elasticsearch' string in page (weak)"),
]


@dataclass
class TechMatch:
    technology: str
    confidence: float
    evidence: str  # the literal matched string


def fingerprint_tech(
    html: str,
    headers: dict[str, str],
    set_cookies: list[str],
    script_srcs: list[str] | None = None,
) -> list[TechMatch]:
    """Detect frameworks/CMS/search backends. Returns one entry per matched signal,
    highest confidence first, so downstream code can decide its own threshold.
    """
    script_srcs = script_srcs or []
    lower_headers = {k.lower(): v for k, v in headers.items()}
    matches: list[TechMatch] = []

    for rule in RULES:
        if rule.source == "html":
            m = rule.pattern.search(html or "")
            if m:
                matches.append(TechMatch(rule.technology, rule.confidence, m.group(0)))
        elif rule.source == "script_src":
            for src in script_srcs:
                m = rule.pattern.search(src)
                if m:
                    matches.append(TechMatch(rule.technology, rule.confidence, src))
                    break
        elif rule.source == "header":
            for name, val in lower_headers.items():
                if rule.pattern.search(name):
                    matches.append(TechMatch(rule.technology, rule.confidence, f"{name}: {val}"))
        elif rule.source == "cookie":
            for c in set_cookies:
                name = c.split("=", 1)[0].strip()
                if rule.pattern.search(name):
                    matches.append(TechMatch(rule.technology, rule.confidence, name))

    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


def wp_json_hint(reachable: bool | None) -> str:
    """Evidence string for whether /wp-json/ responded, appended by the calling
    phase after it makes that (budgeted) probe. Kept out of fingerprint_tech
    itself since that function must stay a pure, no-network classifier.
    """
    if reachable is None:
        return "/wp-json/ not probed"
    return "/wp-json/ responds" if reachable else "/wp-json/ does not respond"
