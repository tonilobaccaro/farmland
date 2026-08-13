"""WAF/anti-bot vendor fingerprinting.

Rules are declarative so waf.json stays auditable: every match records which
signal fired, not just a bare vendor name. Signal sources: response headers,
Set-Cookie names, and body regex. See docs/evidence-gathering-plan.md Part 1C
for the marker table this is transcribed from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WafRule:
    vendor: str
    kind: str  # "header_present" | "header_value" | "cookie_name" | "body_regex"
    key: str  # header name, cookie-name regex, or a human label for body rules
    pattern: re.Pattern[str] | None = None  # required for header_value / body_regex
    description: str = ""


RULES: list[WafRule] = [
    # --- Cloudflare ---------------------------------------------------
    WafRule("Cloudflare", "header_present", "cf-ray", description="CF-RAY header present"),
    WafRule(
        "Cloudflare",
        "header_value",
        "cf-mitigated",
        re.compile(r"challenge", re.IGNORECASE),
        "cf-mitigated: challenge",
    ),
    WafRule(
        "Cloudflare",
        "header_value",
        "server",
        re.compile(r"^cloudflare$", re.IGNORECASE),
        "server: cloudflare",
    ),
    WafRule("Cloudflare", "cookie_name", r"^__cf_bm$", description="__cf_bm cookie"),
    WafRule("Cloudflare", "cookie_name", r"^cf_clearance$", description="cf_clearance cookie"),
    WafRule(
        "Cloudflare",
        "body_regex",
        "challenge-platform script",
        re.compile(r"/cdn-cgi/challenge-platform/"),
        "/cdn-cgi/challenge-platform/ script reference",
    ),
    # --- Akamai Bot Manager --------------------------------------------
    WafRule("Akamai", "cookie_name", r"^_abck$", description="_abck cookie"),
    WafRule("Akamai", "cookie_name", r"^ak_bmsc$", description="ak_bmsc cookie"),
    WafRule("Akamai", "cookie_name", r"^bm_sz$", description="bm_sz cookie"),
    WafRule(
        "Akamai",
        "header_present",
        "x-akamai-transformed",
        description="x-akamai-* header family",
    ),
    # --- DataDome --------------------------------------------------------
    WafRule("DataDome", "cookie_name", r"^datadome$", description="datadome cookie"),
    WafRule("DataDome", "header_present", "x-datadome", description="x-datadome header"),
    WafRule("DataDome", "header_present", "x-dd-b", description="x-dd-b header"),
    WafRule(
        "DataDome",
        "body_regex",
        "datadome script",
        re.compile(r"js\.datadome\.co"),
        "js.datadome.co script reference",
    ),
    # --- HUMAN / PerimeterX -----------------------------------------------
    WafRule("HUMAN (PerimeterX)", "cookie_name", r"^_px\w*$", description="_px* cookie family"),
    WafRule(
        "HUMAN (PerimeterX)",
        "body_regex",
        "pxAppId global",
        re.compile(r"window\._pxAppId\s*=\s*['\"]PX[0-9a-zA-Z]+"),
        "window._pxAppId global",
    ),
    WafRule(
        "HUMAN (PerimeterX)",
        "body_regex",
        "px-cloud script",
        re.compile(r"client\.px-cloud\.net"),
        "client.px-cloud.net script reference",
    ),
    # --- Imperva / Incapsula -----------------------------------------------
    WafRule(
        "Imperva", "cookie_name", r"^incap_ses_\d+_\d+$", description="incap_ses_* cookie"
    ),
    WafRule(
        "Imperva", "cookie_name", r"^visid_incap_\d+$", description="visid_incap_* cookie"
    ),
    WafRule("Imperva", "header_present", "x-iinfo", description="x-iinfo header"),
    # --- Kasada --------------------------------------------------------
    WafRule(
        "Kasada", "header_present", "x-kpsdk-ct", description="x-kpsdk-* header family"
    ),
    WafRule(
        "Kasada", "header_present", "x-kpsdk-cd", description="x-kpsdk-* header family"
    ),
    # --- F5 / Shape --------------------------------------------------------
    WafRule("F5/Shape", "cookie_name", r"^reese84$", description="reese84 cookie"),
    WafRule(
        "F5/Shape",
        "body_regex",
        "TSPD path",
        re.compile(r"/TSPD/"),
        "obfuscated /TSPD/ path reference",
    ),
    # --- AWS WAF --------------------------------------------------------
    WafRule("AWS WAF", "cookie_name", r"^aws-waf-token$", description="aws-waf-token cookie"),
    WafRule("AWS WAF", "cookie_name", r"^awswaf$", description="awswaf cookie"),
    WafRule(
        "AWS WAF", "header_present", "x-amzn-waf-action", description="x-amzn-waf-* header family"
    ),
]


@dataclass
class WafMatch:
    vendor: str
    signal: str  # human-readable description of what matched
    kind: str
    key: str
    raw: str  # the literal value that matched (header value, cookie name, or body excerpt)


@dataclass
class WafFingerprint:
    vendor: str | None
    confidence: float
    matches: list[WafMatch] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "confidence": self.confidence,
            "matches": [
                {"vendor": m.vendor, "signal": m.signal, "kind": m.kind, "key": m.key, "raw": m.raw}
                for m in self.matches
            ],
        }


def _cookie_names(set_cookies: list[str]) -> list[str]:
    names = []
    for c in set_cookies:
        name = c.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def fingerprint_waf(
    headers: dict[str, str],
    set_cookies: list[str],
    body: str,
    status: int | None = None,
) -> WafFingerprint:
    """Score every WAF/anti-bot vendor by matched signals.

    `headers` keys are matched case-insensitively. Returns the best-supported
    vendor (most matches) or vendor=None if nothing fired.
    """
    lower_headers = {k.lower(): v for k, v in headers.items()}
    cookie_names = _cookie_names(set_cookies)
    matches: list[WafMatch] = []

    for rule in RULES:
        if rule.kind == "header_present":
            if rule.key in lower_headers:
                matches.append(
                    WafMatch(rule.vendor, rule.description, rule.kind, rule.key, lower_headers[rule.key])
                )
        elif rule.kind == "header_value":
            val = lower_headers.get(rule.key)
            if val is not None and rule.pattern and rule.pattern.search(val):
                matches.append(WafMatch(rule.vendor, rule.description, rule.kind, rule.key, val))
        elif rule.kind == "cookie_name":
            pat = re.compile(rule.key, re.IGNORECASE)
            for name in cookie_names:
                if pat.match(name):
                    matches.append(WafMatch(rule.vendor, rule.description, rule.kind, rule.key, name))
        elif rule.kind == "body_regex" and rule.pattern:
            m = rule.pattern.search(body or "")
            if m:
                excerpt = body[max(0, m.start() - 20) : m.end() + 20]
                matches.append(WafMatch(rule.vendor, rule.description, rule.kind, rule.key, excerpt))

    # Kasada's signature 429-with-empty-body pattern doesn't fit the declarative
    # table above because it needs `status`, not just headers/cookies/body.
    if status == 429 and not (body or "").strip():
        matches.append(
            WafMatch(
                "Kasada",
                "bare 429 with empty body",
                "status_body",
                "status",
                "429",
            )
        )

    if not matches:
        return WafFingerprint(vendor=None, confidence=0.0, matches=[])

    counts: dict[str, int] = {}
    for m in matches:
        counts[m.vendor] = counts.get(m.vendor, 0) + 1
    best_vendor = max(counts, key=lambda v: counts[v])
    confidence = min(1.0, counts[best_vendor] / 3)  # 3+ independent signals = full confidence
    return WafFingerprint(
        vendor=best_vendor,
        confidence=round(confidence, 2),
        matches=[m for m in matches],
    )
