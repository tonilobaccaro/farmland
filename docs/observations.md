# Observations — corrections to the plan from actually building it

Per the standing instructions in `docs/build-prompts.md`: this file records
where reality (or just the act of building the harness) contradicted what
`docs/evidence-gathering-plan.md` assumed. The plan was written from
research, not observation.

## Prompt 0 / Prompt 1 — built in a sandboxed cloud dev environment

**This is the important one.** Prompts 0 and 1 were executed in a sandboxed
cloud Claude Code session, not "on your own machine" as
`docs/build-prompts.md` requires. That doc predicted exactly this failure
mode ("The container this plan was written in returns `403 CONNECT tunnel
failed` for arbitrary domains") — and it held: `evidence doctor` in this
environment reports outbound HTTPS to an arbitrary host as `403 Forbidden`,
and Chromium is not installed at the path Playwright expects.

Consequence: **Prompt 1's acceptance criteria that depend on live site
behavior are unverified.** `evidence run --site landwatch --phases
p1_recon,p2_static` was run as a smoke test; it completed without raising
(exercising the "any site that blocks completes the phase without raising"
requirement for real, if accidentally), and wrote a fully-formed, honest
`blocked_report.json` (`overall: "blocked"`, `still_accessible: []`) — but
that's because every request failed with `ProxyError: 403 Forbidden`, not
because the fetch ladder, WAF fingerprinting, or challenge detection logic
were validated against a real Cloudflare/Akamai/etc. response. All of
`fetch.py`, `fingerprint/waf.py`, `fingerprint/tech.py`, `recon/*` do have
unit tests against synthetic fixtures (canned headers/cookies/HTML/XML), so
the *logic* is exercised — but nobody has seen `waf.json` name a real vendor
on a real site, or `fetch_ladder.json` show a real per-page-class tier. Treat
Prompt 1's Acceptance section as "code exists and unit tests pass," not "ran
successfully on landwatch/peoplescompany/USDA as specified." **Re-run
`evidence run-wave 1` on a real machine before trusting any of it.**

## Prompt 0 — dependency list was incomplete

Prompt 0's exact dependency list (httpx, curl_cffi, playwright, selectolax,
lxml, extruct, protego, tldextract, w3lib, jsonpath-ng, pdfplumber, jinja2,
pandas, pydantic, typer, rich, pyyaml) has no DNS library, but Prompt 1's
`recon/dns_tls.py` needs A/AAAA/CNAME/MX/TXT records — `socket.getaddrinfo`
only gets A/AAAA. Added `dnspython` in Prompt 1. Expect later prompts to add
a couple more dependencies beyond Prompt 0's list as their scope demands it
(e.g. Prompt 3's bundle/GraphQL work, Prompt 5b's OCR question) — Prompt 0's
list was a reasonable starting set, not a ceiling.

## Prompt 0 — `.gitignore` footgun

The first `.gitignore` used a bare `evidence/` pattern to keep the corpus out
of git. That pattern matches a directory named `evidence` at *any* depth —
including `src/evidence/`, the actual Python package. `git status` briefly
showed the entire codebase as "nothing to commit" because the source tree
itself was gitignored. Fixed by anchoring to the repo root: `/evidence/`.
Anyone extending `.gitignore` here should keep that anchor.

## Prompt 1 — ASN lookup is a stub, not an implementation

The plan (Part 1A) calls for "IP→ASN lookup." Doing this offline needs a
local database (e.g. a MaxMind GeoLite2-ASN `.mmdb` file) that isn't bundled
and wasn't in Prompt 0's dependency list. `recon/dns_tls.lookup_asn_offline()`
is a stub that always returns `{"asn": None, "note": "no offline ASN
database configured"}` rather than silently omitting the field. If ASN
matters enough to build for real, add `maxminddb` + a GeoLite2-ASN file path
in `RunConfig` in a later prompt; for now every site's `dns_tls.json` will
honestly report it wasn't looked up.

## Prompt 1c is still owed

This session went straight from Prompt 0 to Prompt 1 without running the
Prompt 1c hand-trace spike first, because the spike fundamentally requires
loading real pages in a real browser by hand — impossible from this sandbox.
`docs/build-prompts.md` calls Prompt 1c "the most important one to not
skip." **It has not been done yet.** Whoever picks this up on a real machine
should strongly consider running 1c before trusting Prompt 1's untested
assumptions much further, per the doc's own ordering rationale.
