# Build Prompts — Evidence-Gathering Harness

Eight prompts that turn [`docs/evidence-gathering-plan.md`](./evidence-gathering-plan.md) into working code. Each is a self-contained Claude Code session.

**Goal of the whole sequence:** gather and store per-site evidence artifacts on disk. Not to build the scraping agent — that comes later, informed by what these artifacts show.

## How to use

- Run **in order**. Each prompt depends on contracts established by the previous ones.
- One session per prompt. Start fresh; don't chain them in one context.
- Prompt 0 writes `CLAUDE.md` with the shared contracts, so every later session self-orients by reading it.
- Every prompt ends with "commit". Keep commits per-prompt so a bad session is one `git revert`.
- Prompts 1–6 each say "verify on 3 pilot sites." Don't skip that — the artifacts are the product, and a phase that runs without error but writes empty JSON is the main failure mode here.
- If a prompt's acceptance criteria fail because a *site* changed (not the code), that's evidence too. Record it and move on.

---

## Prompt 0 — Scaffold and contracts

````text
Read docs/evidence-gathering-plan.md in full before starting.

Build the skeleton for an evidence-gathering harness that profiles farmland listing
websites. This prompt creates NO collection logic — only the scaffolding, data
contracts, and config that every later phase depends on. Resist the urge to
implement any actual fetching or parsing.

## Deliverables

1. `pyproject.toml` — Python 3.11+, dependencies:
   httpx[http2], curl_cffi, playwright, selectolax, lxml, extruct, protego,
   tldextract, w3lib, jsonpath-ng, pdfplumber, jinja2, pandas, pydantic>=2,
   typer, rich, pyyaml. Dev: pytest, pytest-asyncio, ruff.

2. `src/evidence/models.py` — pydantic v2 models. These are contracts; later
   prompts import them and must not change their field names.

   ```python
   class FetchTier(StrEnum):
       L0 = "L0"  # httpx, default UA
       L1 = "L1"  # httpx, browser headers, http/2
       L2 = "L2"  # curl_cffi, TLS impersonation
       L3 = "L3"  # playwright headless
       L4 = "L4"  # playwright headful
       BLOCKED = "BLOCKED"

   class FetchResult(BaseModel):
       url: str
       final_url: str
       status: int | None
       tier: FetchTier
       http_version: str | None
       headers: dict[str, str]
       set_cookies: list[str]
       body_path: str | None      # artifact-relative path, not inline body
       body_sha256: str | None
       body_bytes: int
       elapsed_ms: int
       redirect_chain: list[str]
       error: str | None
       blocked_reason: str | None  # "waf_challenge" | "403" | "429" | "robots" | ...

   class PhaseResult(BaseModel):
       phase: str
       site: str
       started_at: datetime
       finished_at: datetime
       status: Literal["ok", "partial", "failed", "skipped"]
       artifacts: list[str]        # artifact-relative paths written
       requests_made: int
       notes: list[str]
       error: str | None
   ```

3. `src/evidence/config.py`
   - `SiteConfig`: slug, base_url, wave (1-4), archetype, expected_family, notes,
     optional seed_search_url, optional seed_detail_urls.
   - `RunConfig`: evidence_root, respect_robots (default True), request_budget
     (default 60), min_delay_s (2.0), max_delay_s (5.0), concurrency (1),
     user_agent, contact_url, timeout_s.
   - `load_targets(path) -> list[SiteConfig]`.

4. `targets.yaml` — every site from Part 8 of the plan, in wave order, with slug,
   base_url, wave, archetype and expected_family populated from the plan's
   platform-family table. Waves 1-4. This is data entry; be thorough, all ~60.

5. `src/evidence/artifacts.py` — `ArtifactWriter`. Contract, do not change later:

   ```python
   class ArtifactWriter:
       def __init__(self, site_slug: str, root: Path): ...
       def path(self, rel: str) -> Path      # mkdir parents, return abs path
       def json(self, rel: str, obj: Any) -> str   # returns rel path
       def text(self, rel: str, s: str) -> str
       def bytes(self, rel: str, b: bytes) -> str
       def html(self, rel: str, s: str) -> str
       def exists(self, rel: str) -> bool
       def read_json(self, rel: str) -> Any  # phases read each other's output
   ```
   All writes go under `<root>/<site_slug>/`. JSON writes must be
   `indent=2, sort_keys=True, ensure_ascii=False` so diffs across re-runs are
   readable. Create the exact directory layout from Part 4 of the plan.

6. `src/evidence/politeness.py`
   - `RateLimiter`: per-host, jittered delay between min_delay_s and max_delay_s,
     honors Retry-After, exponential backoff on 429/503.
   - `RequestBudget`: hard cap per site, raises `BudgetExhausted` when hit.
   - `RobotsGate`: wraps protego; `allowed(url) -> bool`; when respect_robots is
     True and a URL is disallowed, record the conflict rather than silently
     skipping — the report must show what we could not look at.

7. `src/evidence/phases/base.py` — `Phase` ABC with
   `name: str`, `depends_on: list[str]`, `async run(ctx: PhaseContext) -> PhaseResult`.
   `PhaseContext` carries SiteConfig, RunConfig, ArtifactWriter, RateLimiter,
   RequestBudget, RobotsGate. Plus a registry: `@register("p1_recon")`.

8. `src/evidence/cli.py` — typer app:
   - `evidence list-sites [--wave N]`
   - `evidence run --site SLUG [--phases p1,p2] [--all-phases] [--force]`
   - `evidence run-wave N [--phases ...]`
   - `evidence status` — table of site × phase × status, read from PhaseResults
   Default behavior is resumable: skip a phase whose PhaseResult exists with
   status ok, unless --force. Every phase writes
   `00_meta/phases/<phase>.json`.

9. `CLAUDE.md` at repo root documenting: the artifact layout, the model
   contracts above, the guardrails from Part 7 of the plan, how to add a phase,
   and the rule that phases communicate ONLY through artifacts on disk (never
   in-memory), so any phase can be re-run alone.

10. `tests/test_scaffold.py` — ArtifactWriter round-trips, targets.yaml parses
    and has >= 55 sites, phase registry resolves dependencies in order.

## Guardrails to encode now, not later
Default user agent names the project and includes contact_url. respect_robots
defaults True. Request budget defaults to 60/site. No CAPTCHA solving, no auth
bypass, no paid bypass services anywhere in this codebase.

## Acceptance
- `evidence list-sites` prints all sites grouped by wave.
- `evidence run --site landwatch --phases none` creates the full empty directory
  tree under evidence/landwatch/.
- `pytest` passes. `ruff check` clean.

Commit as "Scaffold evidence harness: contracts, config, CLI".
````

---

## Prompt 1 — Fetch ladder and passive recon

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md (Parts 1A-1C).

Implement the fetch escalation ladder and the two no-JavaScript phases.

## Deliverables

1. `src/evidence/fetch.py` — `Fetcher` with tiers L0, L1, L2:
   - L0: httpx, default UA, HTTP/1.1
   - L1: httpx, full Chrome header set in correct order, http2=True
   - L2: curl_cffi with impersonate="chrome" (real JA3/JA4 + h2 SETTINGS)
   - `async fetch(url, tier) -> FetchResult`
   - `async escalate(url, max_tier=L2) -> tuple[FetchResult, FetchTier]` — try
     tiers in order, stop at the first that returns real content.
   "Real content" is not just status 200: a Cloudflare interstitial returns 200.
   Classify with `looks_like_challenge(body, headers)` — check for challenge
   markers, suspiciously small body, missing expected page structure.
   Bodies are written via ArtifactWriter and referenced by path, never held in
   the model. All requests go through RateLimiter + RequestBudget + RobotsGate.

2. `src/evidence/fingerprint/waf.py` — rules table mapping evidence to vendor,
   covering at minimum: Cloudflare (CF-RAY, cf-mitigated, __cf_bm, cf_clearance,
   /cdn-cgi/challenge-platform/), Akamai (_abck, ak_bmsc, bm_sz),
   DataDome (datadome cookie, x-datadome, js.datadome.co),
   HUMAN/PerimeterX (_px* cookies, window._pxAppId, client.px-cloud.net),
   Imperva (incap_ses_*, visid_incap_*, x-iinfo),
   Kasada (empty-body 429, x-kpsdk-*), F5/Shape (reese84, /TSPD/),
   AWS WAF (awswaf cookie, x-amzn-waf-*).
   Each rule declares which signal matched, so waf.json is auditable rather than
   a bare vendor name. Signals come from headers, set-cookie, and body regex.

3. `src/evidence/fingerprint/tech.py` — framework/CMS detection from script srcs,
   meta generator, header hints, cookie names, DOM class prefixes. Must detect:
   Next.js, Nuxt, Remix, SvelteKit, Angular, React, Vue, WordPress (+ whether
   /wp-json/ responds), Drupal, ASP.NET WebForms (__VIEWSTATE), Squarespace, Wix,
   Webflow, Algolia, Elasticsearch. Output: list of (technology, confidence,
   evidence) — evidence being the literal string that matched.

4. `src/evidence/recon/` —
   - `dns_tls.py`: A/AAAA/CNAME/MX/TXT, resolved IPs, ASN if resolvable offline,
     TLS cert issuer/subject/SANs/validity, negotiated HTTP version.
     **Save the cert SAN list explicitly** — sibling API hostnames hide there.
   - `robots.py`: fetch raw, parse with protego, extract Sitemap: lines, extract
     Crawl-delay for our UA and for *, and extract every Disallow path as a
     `discovered_path_candidates` list (recon value, per the plan).
   - `sitemap.py`: recursive sitemap-index walk. Caps: depth 3, 50 sitemap files,
     50k URLs. Output tree structure, per-sitemap URL counts, lastmod
     distribution (are they all identical? then lastmod is untrustworthy), and
     URL path-template frequency.
   - `legal.py`: find and snapshot ToS / Terms / Legal / Copyright pages by
     scanning footer links for those words. Save HTML + extracted text. Do not
     attempt to interpret the terms — just capture them.
   - `wellknown.py`: /security.txt, /.well-known/security.txt, /humans.txt,
     /ads.txt, /sitemap.xml, /robots.txt, /openapi.json, /swagger.json, /api/docs.

5. `src/evidence/phases/p1_recon.py` — DNS/TLS, robots, sitemaps, well-known,
   legal. Writes 00_meta/dns_tls.json, 01_policy/*.
   `src/evidence/phases/p2_static.py` — escalate on `/`, on the seed search URL
   if configured, and on one detail URL if configured; fingerprint WAF and tech
   from each; write 00_meta/{headers.json,tech_fingerprint.json,waf.json,
   fetch_ladder.json} and 02_rendering/home_raw.html.

   fetch_ladder.json records the minimum working tier **per page class**
   (home / search / detail), because sites commonly serve the homepage freely and
   challenge only search.

## Acceptance
Run on landwatch, peoplescompany, properties-sc-egov-usda (Wave 1 subset):
- robots.txt and a parsed version exist for all three
- sitemap tree written where sitemaps exist; absence recorded explicitly, not
  silently missing
- waf.json names a vendor with cited evidence for at least one site
- tech_fingerprint.json detects ASP.NET WebForms + __VIEWSTATE on the USDA site
- fetch_ladder.json shows a per-page-class tier for each
- Total requests per site stays under the 60 budget

Commit as "Add fetch ladder and passive recon phases".
````

---

## Prompt 2 — Browser layer, render diff, interaction probes

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md (Part 1D, phases P3-P4).

Add Playwright-based rendering, network capture, and interaction probing.

IMPORTANT: Chromium is pre-installed at PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers.
Do NOT run `playwright install`. If a version mismatch appears, launch with
executablePath='/opt/pw-browsers/chromium'.

## Deliverables

1. `src/evidence/browser.py` — `BrowserSession` async context manager:
   - Chromium, headless by default, realistic viewport and locale
   - HAR recording per navigation via record_har_path
   - `page.on("response")` hook that saves JSON response bodies (HAR often
     truncates or omits them) into 03_network/api_samples/
   - full-page screenshot helper
   - `goto(url, wait_until="networkidle", timeout)` with graceful timeout
     handling — a timeout is a finding, not a crash

2. `src/evidence/render.py`
   - `extract_hydration(page) -> dict` — probe via page.evaluate for
     __NEXT_DATA__, self.__next_f, window.__NUXT__, __remixContext,
     window.__INITIAL_STATE__, window.__APOLLO_STATE__, Angular TransferState
     (ng-state script tag), SvelteKit __data.json, Astro island props.
     Save each found payload as its own file under 02_rendering/hydration/.
   - `render_diff(raw_html, rendered_html) -> RenderDiff` with:
     raw_text_len, rendered_text_len, raw_node_count, rendered_node_count,
     csr_score = (rendered_text - raw_text) / max(rendered_text, 1),
     and **listing_count_delta**: count anchors matching the detail-URL pattern
     in each. The listing delta is the number that decides whether we need a
     browser; text length alone is misleading (nav chrome inflates both).
   - `detect_shadow_dom(page) -> bool` and `extract_noscript(html) -> list[str]`

3. `src/evidence/interact.py` — probes, each returning a typed result and each
   safe to fail:
   - `scroll_probe(page)`: scroll to bottom N times; record node-count growth and
     XHRs fired → classifies infinite scroll vs static
   - `click_load_more(page)`: find buttons matching /load more|show more|view more/i
   - `paginate_probe(page, url)`: find rel=next, numbered pagination links, or
     ?page= params; **fetch page 2 and assert its item set differs from page 1**
   - `submit_search(page)`: locate the primary search form, dump every input,
     select and option value (this is the site's filter vocabulary), submit with
     defaults, record the resulting URL
   - `map_probe(page)`: detect a map (Leaflet/Mapbox/Google/ArcGIS globals or
     canvas), then pan and zoom, capturing XHRs.
     Per the plan this is the highest-value probe for land sites — a viewport
     request often returns every listing in the bbox as one JSON payload.

4. `src/evidence/phases/p3_render.py` — render home + seed search URL, screenshot
   both, dump hydration, write 02_rendering/render_diff.json and HARs.
   `src/evidence/phases/p4_interact.py` — run all probes on the search page,
   write 04_navigation/pagination_probe.json, 04_navigation/search_form.json,
   and additional HARs.

Both phases must be skippable when p2_static determined the site is fully static
AND the listing_count_delta is zero — record the skip reason rather than burning
browser time.

## Acceptance
Run on landwatch, acrevalue, peoplescompany:
- home_rendered.html and home_raw.html both exist and differ measurably
- at least one site yields a non-empty hydration payload file
- render_diff.json csr_score and listing_count_delta populated for all three
- HAR files non-empty, with at least one XHR captured per site
- map_probe finds a map on acrevalue and captures at least one map data request
- pagination_probe proves page 2 differs from page 1 on at least one site

Commit as "Add browser rendering, HAR capture and interaction probes".
````

---

## Prompt 3 — Endpoint mining

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md (Part 1E, phase P5).

Mine the captured network traffic for data interfaces. This phase produces the
single most valuable artifact in the corpus: a ranked list of endpoints that
return listing data without a browser.

## Deliverables

1. `src/evidence/mining/har.py` — parse HARs from 03_network/har/, extract every
   request with method, URL, headers, body, response content-type, size, status.
   Dedupe into URL *templates* by replacing numeric and UUID/slug path segments
   with placeholders, so /api/listing/123 and /api/listing/456 collapse to one.

2. `src/evidence/mining/classify.py` — score each endpoint as a listing-data
   candidate:
   - content-type JSON/GraphQL
   - URL shape: /api/, /api/v1/, /api/v2/, /graphql, /_next/data/, /ajax/,
     /json/, /wp-json/, /services/, /search, /rest/
   - response body contains listing-ish keys — regex over key names for
     price|acre|acreage|listing|property|lat|lng|county|auction
   - response is an array, or an object containing an array of >= 5 similar
     objects
   - size heuristic: bigger JSON payloads outrank tiny config calls
   Output a ranked list with the score breakdown visible, not just a total.

3. `src/evidence/mining/replay.py` — the critical test. For each candidate,
   replay it OUTSIDE the browser at L1 then L2:
   - with full browser headers, with and without cookies, with and without
     Referer/Origin
   - record which combination works → `standalone_viable: true/false` plus the
     minimum header set required
   An endpoint that only works inside a live browser session is worth far less
   than one that answers a bare curl, and the recipe must know which it is.

4. `src/evidence/mining/graphql.py` — POST a standard introspection query to any
   /graphql endpoint. If introspection is disabled, fall back to harvesting
   operation names and persisted-query hashes from the JS bundles.

5. `src/evidence/mining/bundles.py` — download the JS bundles referenced by the
   rendered page (cap: 20 files, 10MB total) and grep for:
   - Algolia appId + search-only apiKey (pattern: appId near a 32-hex key)
   - Elasticsearch/Typesense/Searchspring endpoints and keys
   - Next.js buildId (also available from __NEXT_DATA__)
   - hardcoded API base URLs
   - GraphQL operation strings
   Write 03_network/bundle_secrets.json. These are client-side-public keys by
   design; note that in the artifact so nobody mistakes them for a leak.

6. `src/evidence/mining/nextdata.py` — when Next.js is detected, construct
   /_next/data/<buildId>/<route>.json for the search route and fetch it. This
   frequently returns the entire listing array with zero HTML parsing.

7. `src/evidence/phases/p5_endpoints.py` — orchestrate the above, write
   03_network/endpoints.json (ranked, with replay results), api_samples/*.json,
   graphql_introspection.json, bundle_secrets.json.

## Acceptance
Run on acrevalue, landwatch, peoplescompany, hibid:
- endpoints.json ranked with score breakdowns for all four
- at least two sites yield a standalone_viable endpoint returning listing data
- api_samples/ contains at least one JSON file with recognizable listing fields
  (price, acres, county) for at least two sites
- when Next.js is detected, a /_next/data/ fetch was attempted and its outcome
  recorded either way
- absence of any API is recorded explicitly as a finding, not as a missing file

Commit as "Add endpoint mining: HAR analysis, replay, GraphQL, bundle scanning".
````

---

## Prompt 4 — Navigation mapping and listing-card detection

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md (Part 1G, phase P6).

Answer the question "how do you get from a base URL to pages full of listings",
and "which repeated block on that page is one listing".

## Deliverables

1. `src/evidence/nav/linkgraph.py` — from the rendered home page and search page,
   extract every anchor with href, anchor text, and its DOM region
   (nav / header / footer / main / sidebar, inferred from ancestors). Write
   04_navigation/link_graph.json.

2. `src/evidence/nav/taxonomy.py` — cluster all discovered URLs (from links,
   sitemaps, and HAR) into path templates. Tokenize each path segment as one of:
   literal | numeric | slug | uuid | state-code | state-name | county-name |
   date. Group URLs sharing a token signature. Then label each cluster with a
   role using both the template shape and a sample fetch:
     home | search-index | facet | geo-index | detail | content | asset | api
   Signals for `detail`: singular noun in path, high cluster cardinality, page
   contains exactly one price-like token. Signals for `search-index`: many
   detail-links on the page, pagination present.
   Write 04_navigation/url_taxonomy.json with cluster, role, member count,
   3 example URLs, and the derived regex.

3. `src/evidence/nav/geo.py` — farmland sites are organized geographically.
   Detect state and county index pages (path segments matching state names/codes,
   or link text matching "<County> County"). Enumerate the drill-down path
   base → state → county → listings. Write 04_navigation/geo_index_pages.json.
   This is often the most reliable enumeration route when sitemaps are absent.

4. `src/evidence/nav/cards.py` — repeated-block detection. Implement exactly:
   - compute a structural signature per element: tag + sorted stable class tokens
     (strip hash-like/utility classes) + child tag sequence, depth-limited
   - group siblings sharing a signature; keep groups with >= 3 members
   - score each group: fraction of members containing (a) a link matching the
     detail-URL regex from taxonomy.py, (b) a price-like token, (c) an
     acreage-like token, (d) an image
   - rank; write top 3 candidates to 05_listing_pages/card_candidates.json with
     the container XPath, the per-card XPath, member count, and the score
     breakdown
   - save one isolated card's outerHTML to 05_listing_pages/card_sample.html and
     the container's to card_container.html
   Also extract the "N properties found" total-result count from the page via a
   regex over text near the results region — this is the completeness oracle.

5. `src/evidence/phases/p6_nav.py` — orchestrate; also save search_p1_raw.html,
   search_p1_rendered.html, search_p2_rendered.html and search_p1.png.

## Acceptance
Run on landwatch, farmflip, nationalland, schraderauction:
- url_taxonomy.json labels at least one cluster `detail` and one `search-index`
  per site, with a working regex
- card_candidates.json top candidate resolves to >= 10 cards on a search page for
  at least three of the four sites
- card_sample.html opens in a browser and visibly is one listing
- geo_index_pages.json finds state-level index pages on at least two sites
- total_result_count extracted where the site displays one

Commit as "Add navigation mapping, URL taxonomy and listing-card detection".
````

---

## Prompt 5 — Detail sampling and field evidence

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md (Parts 1F, 1H, 2 — phases P7-P8).

This is the heart of the corpus. For sampled detail pages, record EVERY place
each target field appears, so we can later learn how the same field varies across
sites and presentations.

## Deliverables

1. `src/evidence/ontology.py` — encode the full field ontology from Part 2 of the
   plan as a typed structure. Per field: canonical name, dtype, unit, seed label
   synonyms, seed regex patterns, and JSON-LD paths to check. Cover all groups:
   core, transaction type, price, acreage, soil/productivity, location,
   agronomic, lease, auction, improvements, contact, media.

   Farmland specifics that generic real-estate ontologies miss — get these right:
   - "M/L" and "±" mean "more or less"; strip them, do not parse as a range
   - CSR2 (Iowa), PI (Illinois), NCCPI (national) soil productivity indices
   - PLSS township/range/section is the real geographic key; most tracts have no
     street address, so county+state is the primary location
   - FSA tillable acres and FSA base acres are distinct from total acres
   - price may be given ONLY per-acre, and must be reconstructible from acreage

2. `src/evidence/sampling.py` — `select_samples(site, n=10) -> list[str]`.
   Diversified, NOT random (Part 5 of the plan): 2 auction, 2 private treaty,
   1 sealed bid if present, 1 multi-tract, 1 with no price ("call for price"),
   1 small (<40ac), 1 large (>1000ac), 1 with a brochure PDF, 1 lease.
   Select from the URLs found by p6_nav; classify candidates cheaply from search
   card text before committing to a full fetch. Record which diversity slots were
   filled and which could not be — an unfilled slot is a fact about the site.

3. `src/evidence/structured.py` — extruct wrapper returning JSON-LD, microdata,
   RDFa, OpenGraph and Dublin Core in one pass. Additionally dump ALL data-*
   attributes on the page with their elements (these routinely carry data-lat,
   data-lng, data-acres, data-listing-id), and all HTML comments.

4. `src/evidence/locator.py` — for a given field and page, find every occurrence.
   Search across, in this order, and record ALL hits not just the first:
     jsonld (jsonpath) | api (matching endpoint sample from p5) | opengraph |
     meta | data-attribute | definition list (dt/dd) | table row (th/td) |
     labelled span/div | css selector | free-text regex | pdf text
   Emit the exact `field_evidence.json` shape specified in Part 1H of the plan:
   field, sample_url, gold_value (LEFT NULL for a human to fill), and a
   `locations` array where each entry has kind, the locator expression, and the
   raw matched string.

   Every location must carry the *expression needed to re-find it* (jsonpath,
   CSS selector, XPath, regex) — a location without a reusable expression is
   useless to the agent later.

5. `src/evidence/pdfs.py` — download PDFs linked from detail pages (cap 5/page,
   20MB), extract text with pdfplumber into a sidecar .txt. Record whether text
   extraction returned nothing (scanned image → OCR needed) since that decides a
   scoping question in the plan.

6. `src/evidence/lexicon.py` — aggregate across samples: every spec-table label
   string seen → candidate canonical field (07_fields/label_lexicon.json), and
   every distinct surface form per field (07_fields/value_formats.json). Also
   write 07_fields/field_coverage.md — a human-readable table of which ontology
   fields were found on this site and where.

7. `src/evidence/phases/p7_details.py` and `p8_fields.py`. For each sample write
   06_detail_pages/sample_NN/ with raw.html, rendered.html, screenshot.png,
   jsonld.json, microdata.json, opengraph.json, data_attrs.json, text.txt,
   field_evidence.json, assets/.

## Special attention: multi-tract auctions
Part 2 of the plan flags this as the main schema trap — one Schrader or Peoples
Company auction page lists "Tract 1: 78.5ac, Tract 2: 120ac..." with per-tract
acreage and soil data. Detect tract tables explicitly and write a
`tracts` array in field_evidence.json when found. Ensure at least two multi-tract
pages are sampled on auction-heavy sites. Do not flatten them.

## Acceptance
Run on peoplescompany, schraderauction, landwatch, farmflip:
- 10 sample directories per site, each with raw + rendered HTML and a screenshot
- field_evidence.json present per sample, with >= 3 distinct location kinds
  observed across the site
- multi-tract structure captured on at least one schraderauction sample
- label_lexicon.json non-empty with >= 15 distinct labels per site
- value_formats.json shows genuine variation for acres and price
  (expect "±", "M/L", "Call for Price", "$/acre" among them)
- at least one brochure PDF captured with extracted text, or an explicit record
  that extraction returned nothing

Commit as "Add detail sampling, field evidence location and lexicon aggregation".
````

---

## Prompt 6 — Dynamics, scoring, recipe synthesis, reports

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md (Parts 1I, 4, 8 — phases P9-P10).

Make the corpus browsable and turn each site's evidence into a proposed strategy.

## Deliverables

1. `src/evidence/phases/p9_dynamics.py`
   - conditional GET: re-request with If-None-Match / If-Modified-Since, record
     whether the site answers 304 (enables cheap polling)
   - `--resample` mode: re-fetch a stored search page and diff the listing set
     against the earlier capture; write 08_dynamics/resample_diff.json
   - sold/closed listing behavior: take a detail URL from an earlier run and
     check whether it 404s, redirects, or persists with a status flag
   - sitemap lastmod fidelity: are all values identical or clustered on one date?

2. `src/evidence/scoring.py` — implement Part 8's formula exactly:
   accessibility = 1/fetch_tier (L0=1.0, L1=0.8, L2=0.6, L3=0.4, L4=0.25,
   BLOCKED=0); data_quality = has_api*3 + has_jsonld*2 + has_sitemap*1;
   field_coverage = fields_found/fields_in_ontology;
   volume = log10(estimated_listing_count);
   priority = accessibility * (data_quality + field_coverage) * volume.
   Write each component, not just the product — the components are what make the
   score arguable.

3. `src/evidence/recipe.py` — synthesize 99_report/scrape_recipe.json in exactly
   the shape given in Part 4 of the plan. Strategy selection, in preference
   order: internal_api > hydration_payload > sitemap+static_html >
   paginated_static > rendered_browser > blocked.
   Populate field_map from the aggregated field_evidence, preferring stable
   location kinds (jsonld, api) over brittle ones (nth-child CSS).
   Include a `confidence` and a `notes` array that surfaces robots conflicts,
   unfilled diversity slots, and multi-tract presence.

4. `src/evidence/report.py` + `src/evidence/templates/`
   - `site_report.html` (jinja2): one page per site, rendered FROM the JSON
     artifacts so the human view and the agent view can never disagree.
     Sections: verdict banner (tier, strategy, score), infra + WAF, policy +
     robots conflicts, rendering (raw vs rendered screenshots side by side),
     endpoints table with replay status, navigation + URL taxonomy, card sample
     preview, field coverage matrix (fields × where-found), samples gallery
     linking into each sample dir, dynamics, and the full recipe JSON.
     **Every artifact path must be a working relative link** so you can click
     from the report into the raw evidence. That is the whole point.
   - `site_report.md`: same content, plain text, for reading in a terminal or
     feeding to a model.
   - `_index.html`: dashboard table of all sites — wave, family, tier, WAF,
     has-API, needs-JS, field coverage %, score, link to site report. Sortable
     with inline JS, no external assets (must work from file://).

5. `src/evidence/crosssite.py` — write evidence/_cross_site/:
   - `label_lexicon_global.json` — merged label → canonical field across all sites
   - `value_format_grammar.md` — every surface form per field with a proposed
     parse rule
   - `platform_families.json` — cluster sites by tech fingerprint + cookie names
     + DOM class prefixes + endpoint shapes; report which of the plan's
     hypothesized families are confirmed. Explicitly test whether the Land.com
     network (landwatch/landsofamerica/landandfarm/land.com) is really one
     backend — Part 10 flags this as an open question that shrinks Wave 2.
   - `endpoint_catalog.json` — every endpoint found across all sites
   - `_summary.json` and a pandas-built roll-up feeding _index.html

6. `src/evidence/phases/p10_report.py` — run scoring, recipe, reports, roll-ups.

## Acceptance
- Open evidence/_index.html from file:// — table renders, sorting works, every
  site links to its report
- Open a site_report.html — every artifact link resolves, screenshots display
- scrape_recipe.json produced for every site that completed p1-p8, including a
  `blocked` recipe for sites that could not be reached
- platform_families.json states a verdict on the Land.com hypothesis with cited
  evidence
- value_format_grammar.md contains real observed variants, not invented ones

Commit as "Add dynamics, scoring, recipe synthesis and HTML reporting".
````

---

## Prompt 7 — Full run and triage

````text
Read CLAUDE.md and docs/evidence-gathering-plan.md.

The harness is built. Run it across the full target list and turn the results
into the analysis inputs.

## Tasks

1. Run Wave 1 (10 pilot sites), all phases. Fix crashes as they surface. A site
   that blocks us is NOT a crash — it must complete with a `blocked` recipe and
   a recorded WAF vendor and tier. Verify that distinction holds before going on.

2. Review Wave 1 artifacts by hand. For each site, open site_report.html and
   confirm the evidence actually supports the recipe it proposes. Where it does
   not, fix the phase, not the report.

3. Fill `gold_value` on 3 samples per Wave 1 site by hand (30 pages total). This
   is the eval set — without it there is no way to score extraction later. Store
   as 06_detail_pages/sample_NN/gold.json so re-runs never overwrite it.

4. Run Waves 2, 3, 4. Respect the request budget; expect this to take hours of
   wall clock given the politeness delays. Run sites sequentially, not in
   parallel — the delays exist for a reason.

5. Write `evidence/_cross_site/decision_tree.md` — the playbook the agent starts
   from, derived from what the corpus actually shows, not from priors. Structure
   it as ordered checks, e.g. "check hydration payload before rendering",
   "check /wp-json/wp/v2/ on any WordPress site", "probe the map viewport
   endpoint on any site with a map". Every rule must cite the sites that
   support it.

6. Write `docs/findings.md` summarizing for the agent design question:
   - which platform families were confirmed, and how much they collapse the work
   - distribution of strategies across sites (how many are API-able vs
     browser-required vs blocked)
   - which ontology fields are reliably available vs rarely present
   - where field values vary most across sites (this is what the agent must be
     robust to)
   - how often multi-tract auctions appear, and the recommended schema decision
   - the four open questions from Part 10 of the plan, now answered with data

## Guardrails
Unchanged and non-negotiable: robots respected, 60-request budget per site,
sequential, no CAPTCHA solving, no auth bypass, no paid bypass services. A site
requiring L4+ is marked blocked and skipped — that is a finding, not a failure.

## Acceptance
- evidence/_index.html lists every attempted site with a terminal status
- no site left in a crashed or half-written state
- 30 hand-labeled gold samples exist
- decision_tree.md and docs/findings.md written, every claim citing evidence
  paths

Commit progressively — one commit per wave, plus one for the findings.
````

---

## Prompt 8 — Hardening (optional, run after first full pass)

````text
Read CLAUDE.md and docs/findings.md.

Harden the harness for repeat runs now that we know how sites actually behave.

1. Tests: golden-file tests for card detection, URL taxonomy clustering, field
   locator and value normalization, using saved HTML fixtures from the corpus
   (not live requests). Fixtures under tests/fixtures/, trimmed for size.
2. Value normalizers: implement and test the parse rules from
   value_format_grammar.md — acreage with ±/M-L, price per-acre reconstruction,
   auction datetime with timezone from prose ("10:00 AM CST"), PLSS parsing,
   county/state normalization against a canonical FIPS list.
3. Re-run resilience: `evidence run --site X --diff-previous` to compare a fresh
   run against the stored one and report what changed on the site. Site drift is
   the main long-term risk to any recipe.
4. Redaction pass: confirm no credentials, session cookies or personal data
   beyond published broker contacts landed in the corpus before it is shared.
5. Corpus size: report total bytes; trim HARs to headers + JSON bodies if the
   corpus exceeds a few GB.

Commit as "Harden harness: tests, normalizers, drift detection".
````

---

## Notes on running these

**Where the value concentrates.** Prompts 3 and 5 produce most of the corpus's worth. Prompt 3 finds the endpoints that make scraping cheap; Prompt 5 produces the field-variation evidence that is the entire reason to do this rather than hand-writing four scrapers. If time is short, do 0–5 properly and treat 6–8 as follow-on.

**The gold labels in Prompt 7 step 3 are tedious and load-bearing.** Thirty hand-checked pages is the difference between "the agent seems to work" and a measurable per-field accuracy number. Don't let a session skip it by generating plausible values — the whole point is that a human verified them.

**Sequencing risk.** Prompts 1–6 each add a phase that reads the previous phase's artifacts. If you reorder them, later phases will read files that don't exist yet. The dependency chain is p1 → p2 → p3 → p4 → p5 → p6 → p7 → p8 → p9 → p10, and `depends_on` in the phase registry should enforce it.

**When a prompt's acceptance criteria fail.** Distinguish three cases and say which one you're in: the code is wrong (fix it), the site changed (record it), or the expectation was wrong (amend the plan). The third is common and fine — the plan was written from research, not from observation, and the corpus is what corrects it.
