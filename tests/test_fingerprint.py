from __future__ import annotations

from evidence.fingerprint.tech import fingerprint_tech, wp_json_hint
from evidence.fingerprint.waf import fingerprint_waf

# ---------------------------------------------------------------------------
# WAF fingerprinting
# ---------------------------------------------------------------------------


def test_cloudflare_detected_from_header_and_cookie():
    headers = {"CF-RAY": "83af9-LAX", "server": "cloudflare"}
    cookies = ["__cf_bm=abc123; Path=/; HttpOnly"]
    fp = fingerprint_waf(headers, cookies, body="<html>hi</html>", status=200)
    assert fp.vendor == "Cloudflare"
    assert fp.confidence > 0
    assert any(m.kind == "cookie_name" for m in fp.matches)


def test_cloudflare_challenge_header_detected():
    headers = {"cf-mitigated": "challenge"}
    fp = fingerprint_waf(headers, [], body="", status=403)
    assert fp.vendor == "Cloudflare"


def test_akamai_detected_from_cookies():
    cookies = ["_abck=xyz; Path=/", "ak_bmsc=zzz; Path=/", "bm_sz=qqq; Path=/"]
    fp = fingerprint_waf({}, cookies, body="", status=200)
    assert fp.vendor == "Akamai"
    assert len(fp.matches) == 3
    assert fp.confidence == 1.0  # 3+ independent signals


def test_datadome_detected_from_header_and_script():
    headers = {"x-datadome": "1"}
    body = '<script src="https://js.datadome.co/tags.js"></script>'
    fp = fingerprint_waf(headers, [], body=body, status=200)
    assert fp.vendor == "DataDome"


def test_perimeterx_detected_from_cookie_and_global():
    cookies = ["_pxvid=abcd; Path=/"]
    body = "window._pxAppId = 'PX1a2b3c4d';"
    fp = fingerprint_waf({}, cookies, body=body, status=200)
    assert fp.vendor == "HUMAN (PerimeterX)"


def test_imperva_detected_from_cookies_and_header():
    headers = {"x-iinfo": "1-abc"}
    cookies = ["incap_ses_123_456=xxx; Path=/", "visid_incap_789=yyy; Path=/"]
    fp = fingerprint_waf(headers, cookies, body="", status=200)
    assert fp.vendor == "Imperva"


def test_kasada_bare_429_empty_body():
    fp = fingerprint_waf({}, [], body="", status=429)
    assert fp.vendor == "Kasada"


def test_kasada_headers():
    headers = {"x-kpsdk-ct": "1", "x-kpsdk-cd": "1"}
    fp = fingerprint_waf(headers, [], body="", status=200)
    assert fp.vendor == "Kasada"


def test_f5_shape_detected():
    cookies = ["reese84=abcdef; Path=/"]
    fp = fingerprint_waf({}, cookies, body="/TSPD/09abc123", status=200)
    assert fp.vendor == "F5/Shape"


def test_aws_waf_detected():
    headers = {"x-amzn-waf-action": "challenge"}
    fp = fingerprint_waf(headers, [], body="", status=200)
    assert fp.vendor == "AWS WAF"


def test_no_waf_signals_returns_none_vendor():
    fp = fingerprint_waf({"content-type": "text/html"}, [], body="<html><body>hi</body></html>", status=200)
    assert fp.vendor is None
    assert fp.matches == []


def test_waf_to_dict_shape():
    fp = fingerprint_waf({"CF-RAY": "1"}, [], body="", status=200)
    d = fp.to_dict()
    assert d["vendor"] == "Cloudflare"
    assert isinstance(d["matches"], list)
    assert d["matches"][0]["signal"]


# ---------------------------------------------------------------------------
# Tech fingerprinting
# ---------------------------------------------------------------------------


def test_nextjs_detected_from_hydration_marker():
    html = '<html><body><script id="__NEXT_DATA__" type="application/json">{}</script></body></html>'
    matches = fingerprint_tech(html, {}, [], [])
    names = {m.technology for m in matches}
    assert "Next.js" in names


def test_wordpress_detected_from_generator_meta_and_paths():
    html = '<meta name="generator" content="WordPress 6.4" /><script src="/wp-content/themes/x/app.js"></script>'
    matches = fingerprint_tech(html, {}, [], ["/wp-content/themes/x/app.js"])
    names = {m.technology for m in matches}
    assert "WordPress" in names
    assert max(m.confidence for m in matches if m.technology == "WordPress") >= 0.85


def test_aspnet_webforms_detected_from_viewstate():
    html = '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUKMTY4MDEy" />'
    matches = fingerprint_tech(html, {}, [], [])
    names = {m.technology for m in matches}
    assert "ASP.NET WebForms" in names


def test_matches_sorted_by_confidence_descending():
    html = (
        '<meta name="generator" content="WordPress 6.4" />'
        '<script src="/wp-content/themes/x/app.js"></script>'
    )
    matches = fingerprint_tech(html, {}, [], ["/wp-content/themes/x/app.js"])
    confidences = [m.confidence for m in matches]
    assert confidences == sorted(confidences, reverse=True)


def test_no_matches_on_plain_html():
    matches = fingerprint_tech("<html><body>hello</body></html>", {}, [], [])
    assert matches == []


def test_wp_json_hint_strings():
    assert "responds" in wp_json_hint(True)
    assert "does not respond" in wp_json_hint(False)
    assert "not probed" in wp_json_hint(None)
