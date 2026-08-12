# Evidence-Gathering Plan for a Farmland Listing Scraper Agent

**Status:** plan only — no code written yet. This document specifies what to build.
**Goal:** produce a browsable corpus of per-site evidence (HTML, JSON, HAR, screenshots, PDFs, reports) that tells us, for every target farmland site: *how do you get from a base URL to listing pages, and how do you pull the fields we want off those pages.*

The corpus has three consumers:

1. **A human (you)** — open `evidence/<site>/99_report/site_report.html` and understand the site in five minutes.
2. **The agent at build time** — the corpus becomes few-shot examples, a label lexicon, and a value-format grammar that generalize across sites.
3. **The agent at eval time** — hand-checked field values on sampled detail pages become a gold set to score extraction against.

---

## Part 1 — What "evidence" means

Nine categories. Everything the agent could reasonably want to know before committing to a scraping strategy.

### A. Identity & infrastructure

| Evidence | Why it matters | How to collect |
|---|---|---|
| DNS records, resolved IPs, ASN | CDN/host identity predicts defense posture | `dnspython`, IP→ASN lookup |
| TLS certificate (issuer, SANs) | SANs leak sibling domains and API hosts | `ssl.getpeercert()` |
| HTTP version negotiated (h1/h2/h3) | h2-only + strict SETTINGS is an anti-bot signal | `httpx` with `http2=True` |
| Response headers on `/` | `server`, `x-powered-by`, `via`, `x-cache` | plain GET |
| Tech fingerprint | Framework determines where the data hides | Wappalyzer-style rule pass over HTML + headers + script src |

### B. Access policy & legal

| Evidence | Why it matters | How to collect |
|---|---|---|
| `robots.txt` (raw + parsed) | Governs what we crawl; also *leaks* paths in `Disallow` | fetch + `protego` |
| Crawl-delay / Request-rate directives | Sets our politeness floor | parsed robots |
| Sitemap references | Fastest possible listing enumeration | robots `Sitemap:` lines + `/sitemap.xml` probe |
| Full sitemap tree | Counts, `lastmod` fidelity, URL templates | recursive sitemap-index walk, cap depth |
| Terms of Service / legal page snapshot | Scraping clauses — record, don't interpret away | fetch + save HTML + extract text |
| `/security.txt`, `/humans.txt`, `/ads.txt` | Occasional contact info for permission requests | fetch |
| Rate-limit response headers | `Retry-After`, `RateLimit-*`, `X-RateLimit-*` | observed across the run |

**Note on `Disallow` as reconnaissance:** a `Disallow: /api/internal/` line is free intelligence about endpoint structure. Record it as a discovered path candidate; whether we *call* it is a separate policy decision (see Guardrails).

### C. Bot / WAF defense posture

Fingerprint the vendor from headers, cookies, script sources, and JS globals. Known markers:

| Vendor | Markers |
|---|---|
| Cloudflare | `CF-RAY` header, `cf-mitigated: challenge`, `server: cloudflare`, `__cf_bm` / `cf_clearance` cookies, `/cdn-cgi/challenge-platform/` script |
| Akamai Bot Manager | `_abck`, `ak_bmsc`, `bm_sz` cookies, `x-akamai-*`, sensor POST to a random path |
| DataDome | `datadome` cookie, `x-datadome` / `x-dd-b` headers, `js.datadome.co` script |
| HUMAN (ex-PerimeterX) | `_px*` cookies, `window._pxAppId` (format `PX1a2b3c4d`), `client.px-cloud.net` |
| Imperva / Incapsula | `incap_ses_*`, `visid_incap_*` cookies, `x-iinfo` header |
| Kasada | bare `429` with empty body, `x-kpsdk-*` headers, `/149e9513-…/2d206a39-…` script paths |
| F5 / Shape | `reese84` cookie, obfuscated `/TSPD/` paths |
| AWS WAF | `awswaf` token cookie, `x-amzn-waf-*` |

Collect: which vendor (if any), whether the *homepage* challenges vs only the *search/detail* pages, and — critically — **the escalation tier required** (below).

### C-bis. The fetch escalation ladder

The single most actionable output per site. Try tiers in order; record the lowest that returns real content:

| Tier | Method | Cost |
|---|---|---|
| **L0** | `httpx`, default UA, HTTP/1.1 | trivial |
| **L1** | `httpx`, full browser header set, HTTP/2, correct header *order* | trivial |
| **L2** | `curl_cffi` with `impersonate="chrome"` — real JA3/JA4 TLS + h2 SETTINGS fingerprint | cheap |
| **L3** | Playwright headless Chromium | ~1s/page, memory |
| **L4** | Playwright headful + stealth patches, human-ish timing | slow |
| **L5** | Any of the above via residential/mobile proxy | $$ — **flagged, not executed in this project** |

Record per site *and per page class* (home / search / detail / API) — many sites serve static HTML happily but challenge the search endpoint.

### D. Rendering model

| Evidence | How to collect |
|---|---|
| Raw HTML vs post-JS DOM | fetch both; save both; diff |
| CSR score | `(rendered_text_len - raw_text_len) / rendered_text_len`, plus DOM node delta, plus **listing-count delta** (the one that actually matters) |
| Hydration payload | grep for `__NEXT_DATA__`, `self.__next_f.push`, `window.__NUXT__`, `__remixContext`, `window.__INITIAL_STATE__`, `window.__APOLLO_STATE__`, Angular `ng-state` / TransferState, SvelteKit `__data.json`, Astro island props |
| `<noscript>` fallbacks | sometimes contains a full non-JS listing table |
| Lazy-load / infinite scroll / virtualized list | scroll probe: does DOM node count grow? do new XHRs fire? |
| Shadow DOM usage | breaks naive selectors; detect via `document.querySelectorAll('*')` shadowRoot walk |

**Key insight to encode:** a hydration payload is a *free API*. If `__NEXT_DATA__` on a search page contains the full listing array, we never need a browser and never need to parse HTML.

### E. Data interfaces — the prize

Ranked by desirability. The agent should always prefer the highest-ranked available.

1. **Documented public API** — rare, but check `/api/docs`, `/developers`, `/swagger.json`, `/openapi.json`.
2. **Internal JSON/REST API** — captured from XHR/fetch traffic. Look for `/api/v1/`, `/api/v2/`, `/services/`, `/ajax/`, `/json/`.
3. **GraphQL** — `/graphql`, `/gql`. Attempt introspection; if disabled, harvest operations from the JS bundle (persisted-query hashes are usually inlined).
4. **Next.js route data** — `/_next/data/<buildId>/<route>.json`. Extract `buildId` from `__NEXT_DATA__`.
5. **Search-backend keys in the JS bundle** — Algolia (`appId` + search-only `apiKey`), Elasticsearch, Typesense, Searchspring. These are *client-side by design* and usually give clean paginated JSON.
6. **Map / marker endpoints** — hugely important for land sites. A map viewport request often returns **every listing in the bounding box in one JSON payload**, with lat/lng, acres, and price already parsed. Probe by panning/zooming the map.
7. **Feeds & exports** — RSS/Atom, CSV/XLSX download buttons, KML/GeoJSON/shapefile downloads, iCal for auction calendars.
8. **Real-estate-specific protocols** — IDX feeds, RETS, **RESO Web API** (OData). Many brokerage sites are thin skins over one of these.
9. **Auction-platform APIs** — see Platform Families below.
10. **Static HTML parsing** — the fallback.

For every discovered endpoint record: method, URL template, query/body params (and which ones look like pagination/filters), auth requirements (cookie? bearer? signed? none?), response shape, whether it works when replayed *outside* the browser, rate-limit behavior, and a saved sample response.

### F. Structured metadata on the page

Cheap, stable, and criminally underused. Extract with `extruct` (handles all of these in one pass):

- **JSON-LD** — `RealEstateListing`, `Product` + `Offer`, `Place`, `GeoCoordinates`, `Event` (auctions!), `ItemList` (search results!), `BreadcrumbList` (navigation topology!), `Organization`.
- **Microdata / RDFa** — older sites, same vocabulary.
- **OpenGraph / Twitter cards** — `og:title`, `og:description`, `og:image`, `product:price:amount`, `og:latitude`.
- **`<meta>`** — canonical, description, hreflang, custom `property-*` metas.
- **`data-*` attributes** — regularly carry `data-lat`, `data-lng`, `data-acres`, `data-listing-id`, `data-price` on card elements. Dump all of them.
- **HTML comments** — sometimes leak template variable names or CMS identity.

`ItemList` and `BreadcrumbList` deserve special mention: they answer "which links on this page are listings?" and "what is the site's category hierarchy?" without any heuristics.

### G. Navigation topology — base URL → listing pages

This is the half of the agent's job that isn't field extraction.

| Evidence | How to collect |
|---|---|
| Nav/menu link graph | parse `<nav>`, `<header>`, `<footer>`; save anchor text + href |
| URL taxonomy | cluster all discovered URLs into path templates (`/land/{state}/{county}/{slug}`), label each cluster: `home` / `search-index` / `facet` / `detail` / `content` / `asset` |
| Geographic index pages | farmland sites almost always have `/state/` → `/state/county/` drill-downs — enumerate them |
| Search form | `<form>` action, method, all inputs/selects with option values (this *is* the filter vocabulary) |
| Pagination mechanism | classify: `?page=N` \| `?offset=&limit=` \| cursor token \| `rel="next"` link \| "Load more" button \| infinite scroll |
| Total result count | scrape "1,234 properties found" — the completeness oracle |
| Page-2 proof | actually fetch page 2 and confirm the item set differs |
| Detail-URL template | regex derived from links found on search pages |
| Card block detection | find repeated DOM subtrees: ≥3 siblings with matching tag+class signature, each containing a link matching the detail template plus a price-like or acre-like token. Save top-3 candidate XPaths and one isolated card's HTML. |

### H. Field-level evidence on detail pages

For each of ~10 sampled detail pages per site, and for each target field, record **every** place the value appears:

```json
{
  "field": "acres_total",
  "sample_url": "https://…/farm-for-sale/…",
  "gold_value": 160.0,
  "locations": [
    {"kind": "jsonld",  "path": "$['@graph'][0].floorSize.value", "raw": "160"},
    {"kind": "api",     "endpoint": "/api/v2/listings/8471", "json_path": "$.data.acreage", "raw": 160},
    {"kind": "css",     "selector": ".property-stats li:nth-child(2) .value", "raw": "160± Acres"},
    {"kind": "table",   "row_label": "Total Acres", "raw": "160 AC M/L"},
    {"kind": "regex",   "pattern": "([\\d,.]+)\\s*(?:±|\\+/-)?\\s*(?:acres|ac\\b)", "raw": "160± Acres"},
    {"kind": "og",      "property": "product:size", "raw": "160"},
    {"kind": "pdf",     "file": "assets/brochure.pdf", "page": 1, "raw": "160.00 FSA cropland acres"}
  ]
}
```

Aggregate across pages and sites into:

- **`label_lexicon.json`** — every spec-table label string seen, mapped to canonical field. This is the synonym dictionary the agent needs.
- **`value_formats.json`** — every distinct surface form per field, which becomes a normalization grammar.

### I. Change dynamics & cost

- Conditional GET support (`ETag`, `Last-Modified` → `304`) — enables cheap polling.
- Re-sample the same search page after 24h; diff. How volatile is inventory?
- Do sold/closed listings 404, redirect, or persist with a status flag? (Determines whether we can build price history.)
- `lastmod` fidelity in sitemaps — trustworthy or always "today"?
- Bytes and wall-clock per listing at the chosen tier → cost model.

---

## Part 2 — The farmland field ontology

What the agent must extract. Grouped, with the messy-reality notes that drive the evidence design.

**Core**
`listing_id` · `source_site` · `source_url` · `canonical_url` · `title` · `description` · `status` (active/pending/sold/withdrawn) · `first_seen` · `last_seen`

**Transaction type** — `listing_type`: `private_treaty` | `auction` | `sealed_bid` | `lease` | `sold_comp`. Sites conflate these; some run both on one page ("Auction — or buy now").

**Price** — `price_total` · `price_per_acre` · `price_type` (asking / opening bid / reserve / sold / undisclosed) · `currency`.
Surface forms: `$1,250,000`, `$12,500/acre`, `$12,500 per acre`, `Call for Price`, `Contact Agent`, `Auction`, `TBD`, `Starting Bid $500,000`, `Sold $1.4M`. Per-acre is sometimes the *only* price given — must be reconstructible from acres.

**Acreage** — `acres_total` · `acres_tillable` · `acres_cropland_fsa` · `acres_pasture` · `acres_timber` · `acres_cra` (CRP) · `acres_wetland` · `tillable_pct`.
Surface forms: `160±`, `160.5 Acres`, `±160 ac`, `160 AC M/L` ("more or less"), `160 acres m/l`, `88% tillable`, `142 FSA tillable acres`. The `±` / `M/L` convention is near-universal in farmland and must be stripped, not parsed as a range.

**Soil & productivity** — `csr2` (Iowa Corn Suitability Rating 2) · `productivity_index` (Illinois PI) · `nccpi` (national) · `soil_types[]` · `soil_class`.
These are farmland-specific and often live only in a PDF brochure or a soil-map image — a major reason PDF capture is in scope.

**Location** — `address` · `city` · `county` · `state` · `zip` · `lat` · `lng` · `plss_township` / `plss_range` / `plss_section` · `parcel_id` (APN) · `school_district`.
County + state is the primary geographic key for farmland (not street address — most tracts have none). PLSS legal descriptions appear constantly in the Midwest and are a reliable join key.

**Agronomic** — `crop_types[]` (corn/soy/wheat/cotton/rice/hay/permanent crops) · `land_use` (row crop / pasture / ranch / timber / orchard / vineyard / dairy) · `irrigation` (dryland / pivot / flood / drip) · `pivot_count` · `water_rights` · `drainage_tile` · `fsa_base_acres` · `yield_history`.

**Lease/income** — `current_lease_status` · `cash_rent` · `lease_expiry` · `possession_date`.

**Auction-specific** — `auction_start` · `auction_end` (with **timezone** — usually stated as "10:00 AM CST" in prose only) · `auction_format` (live / online-only / hybrid / simulcast / sealed bid) · `bid_deadline` · `auction_venue` · `bidding_platform` · `tract_count` (multi-parcel auctions sell N tracts on one page — **one listing page can be many listings**) · `buyer_premium_pct` · `bid_increment` · `earnest_money` · `closing_date`.

**Improvements** — `buildings[]` · `grain_storage_bu` · `home_on_property` · `outbuildings`.

**Contact & provenance** — `broker_firm` · `agent_name` · `agent_phone` · `agent_email` · `listing_date` · `mls_number`.

**Media** — `photos[]` · `documents[]` (brochure PDF, soil map, FSA-156EZ, survey, offering memorandum) · `maps[]` (KML/aerial/plat).

> **The multi-tract problem is the biggest modeling trap here.** A Schrader or Peoples Company auction page routinely lists "Tract 1: 78.5 ac, Tract 2: 120 ac …" with per-tract acreage and soil data under a single URL. The evidence gatherer must explicitly capture examples of this so the schema decides early: one row per page, or one row per tract with a parent auction ID. Sample at least two multi-tract pages per auction-heavy site.

---

## Part 3 — Platform families (the force multiplier)

Most target sites are not bespoke. Grouping them means one recipe covers many domains — the plan should verify these groupings early because it collapses the work.

| Family | Members | Expected shape |
|---|---|---|
| **Land.com network** (CoStar) | land.com, landwatch.com, landsofamerica.com, landandfarm.com | Shared backend, near-identical DOM; one recipe → four sites. High-value first probe. |
| **LandFlip network** | landflip, farmflip, ranchflip, lotflip, auctionflip | Same. |
| **HiBid-powered auctioneers** | hibid.com + hundreds of auctioneer subdomains | Uniform lot/auction JSON structure |
| **Proxibid-powered** | proxibid.com + hosted auctioneers | Uniform |
| **BidWrangler / NextLot white-label** | many regional farm auctioneers on own domains | Same app under different CSS — detect by script src |
| **WordPress + real-estate theme** | the majority of regional brokerages | `/wp-json/wp/v2/` often exposes listings as a custom post type — check this first, always |
| **IDX / RESO Web API skins** | many brokerage sites | Data available via OData; the HTML is a rendering artifact |
| **ASP.NET WebForms (legacy gov)** | USDA RD/FSA resales (`properties.sc.egov.usda.gov`) | `__VIEWSTATE` / `__EVENTVALIDATION` POST flow, session-stateful — needs its own recipe |
| **Bespoke** | Peoples Company, Schrader, Farmers National, AcreValue, AcreTrader | Individual treatment |

**Detection method:** fingerprint from script sources, cookie names, HTML generator meta, and DOM class-name prefixes; cluster sites by fingerprint similarity before writing any per-site code.

---

## Part 4 — Output layout

One directory per site, browsable in a file manager or a static server. `_index.html` at the root links everything.

```
evidence/
├── _index.html                    # dashboard: every site, tier, WAF, API?, JS?, score
├── _summary.json                  # machine-readable roll-up
├── _cross_site/
│   ├── label_lexicon_global.json  # all spec labels → canonical field
│   ├── value_format_grammar.md    # every surface form per field, with parse rules
│   ├── platform_families.json     # fingerprint clusters
│   ├── endpoint_catalog.json      # every API found, all sites
│   └── decision_tree.md           # "if X then strategy Y" — the agent's playbook
└── landwatch.com/
    ├── 00_meta/          run.json · dns_tls.json · headers.json · tech_fingerprint.json · waf.json · fetch_ladder.json
    ├── 01_policy/        robots.txt · robots_parsed.json · terms_of_service.html · sitemaps/{sitemap_index.xml,tree.json,url_patterns.json}
    ├── 02_rendering/     home_raw.html · home_rendered.html · home_raw.png · home_rendered.png · render_diff.json · hydration/__NEXT_DATA__.json
    ├── 03_network/       har/{home,search,detail}.har · endpoints.json · api_samples/*.json · graphql_introspection.json · bundle_secrets.json
    ├── 04_navigation/    link_graph.json · url_taxonomy.json · search_form.json · pagination_probe.json · geo_index_pages.json
    ├── 05_listing_pages/ search_p1_raw.html · search_p1_rendered.html · search_p2_rendered.html · search_p1.png · card_candidates.json · card_sample.html
    ├── 06_detail_pages/  sample_01/{raw.html,rendered.html,screenshot.png,jsonld.json,microdata.json,opengraph.json,data_attrs.json,text.txt,field_evidence.json,assets/*.pdf} … sample_10/
    ├── 07_fields/        label_lexicon.json · value_formats.json · field_coverage.md
    ├── 08_dynamics/      conditional_get.json · resample_diff.json
    └── 99_report/        site_report.md · site_report.html · scrape_recipe.json
```

`scrape_recipe.json` is the payoff — the machine-readable strategy the agent would emit:

```json
{
  "site": "landwatch.com",
  "fetch_tier": "L2",
  "strategy": "internal_api",
  "enumeration": {
    "method": "sitemap",
    "sitemap_url": "https://…/sitemap-listings-1.xml",
    "fallback": {"method": "paginated_search", "url_template": "…?page={n}", "max_page_probe": true}
  },
  "listing_source": {
    "kind": "json_api",
    "url_template": "/api/v2/search?state={state}&page={n}",
    "items_path": "$.results",
    "pagination": {"kind": "page_param", "param": "page", "total_path": "$.total"}
  },
  "field_map": {
    "acres_total": [{"kind":"json_path","expr":"$.acreage"},{"kind":"css","expr":".stats .acres"}],
    "price_total": [{"kind":"json_path","expr":"$.price"}]
  },
  "confidence": 0.86,
  "notes": ["multi-tract auctions not present", "robots disallows /search/ — enumerate via sitemap only"]
}
```

---

## Part 5 — Pipeline

Ten phases, each writing artifacts and each independently re-runnable per site.

| Phase | Does | Writes |
|---|---|---|
| **P0 Seed** | load `targets.yaml`, resolve base URLs, create dirs | `00_meta/run.json` |
| **P1 Passive recon** | DNS, TLS, headers, robots, sitemaps, ToS, `/security.txt`. No JS. | `00_meta/`, `01_policy/` |
| **P2 Static fetch + ladder** | run L0→L2, record lowest working tier per page class; fingerprint WAF and tech | `fetch_ladder.json`, `waf.json`, `home_raw.html` |
| **P3 Render + capture** | Playwright: load home + candidate search page, record HAR, screenshot full-page, dump hydration payloads and final DOM | `02_rendering/`, `03_network/har/` |
| **P4 Interaction probes** | click pagination / "load more", scroll to bottom, submit search form, pan+zoom the map; capture resulting XHRs | `pagination_probe.json`, more HAR |
| **P5 Endpoint mining** | dedupe HAR entries, classify JSON/GraphQL candidates, replay each *outside* the browser to test standalone viability, attempt GraphQL introspection, grep JS bundles for Algolia/Elastic keys and API base URLs | `endpoints.json`, `api_samples/`, `bundle_secrets.json` |
| **P6 Navigation mapping** | build link graph, cluster URLs into templates, label roles, find geo index pages, detect card blocks on the search page | `04_navigation/`, `05_listing_pages/card_candidates.json` |
| **P7 Detail sampling** | pick **10 diversified samples** (see below), fetch raw + rendered, screenshot, extract all structured metadata, download linked PDFs | `06_detail_pages/sample_*/` |
| **P8 Field evidence** | for each sample × each ontology field, locate every occurrence; hand-verifiable `gold_value` slot left blank for human fill; aggregate lexicon and formats | `field_evidence.json`, `07_fields/` |
| **P9 Dynamics** | conditional GET test; schedule a 24h re-sample and diff | `08_dynamics/` |
| **P10 Synthesis** | score the site, emit `scrape_recipe.json`, render `site_report.html`, update cross-site roll-ups and `_index.html` | `99_report/`, `_cross_site/` |

**Detail-page sampling must be diversified, not random.** Pick to maximize variation: 2 auction + 2 private treaty + 1 sealed bid (if present), 1 multi-tract, 1 with no price ("call for price"), 1 tiny (<40 ac) + 1 large (>1000 ac), 1 with a brochure PDF, 1 lease listing. Diversity is what makes the corpus teach the agent about variation rather than about one template.

---

## Part 6 — Tech stack

| Need | Choice | Note |
|---|---|---|
| HTTP | `httpx` (HTTP/2) | L0/L1 |
| TLS impersonation | `curl_cffi` | L2 — cheap Cloudflare/Akamai passthrough |
| Browser | `playwright` (Chromium) | already at `/opt/pw-browsers`; **do not** run `playwright install` |
| HAR capture | Playwright `record_har_path` | plus `page.on("response")` for bodies |
| HTML parse | `selectolax` (fast) + `lxml` (XPath) | |
| Structured data | `extruct` | JSON-LD + microdata + RDFa + OG in one call |
| robots | `protego` | Google-compatible parsing |
| Sitemaps | custom recursive walker | with depth + URL caps |
| URL work | `tldextract`, `w3lib`, `furl` | |
| JSON querying | `jsonpath-ng` | for field_evidence paths |
| PDFs | `pdfplumber` | brochure text; note OCR gap for scanned soil maps |
| Reports | `jinja2` | HTML from the same JSON the agent reads |
| Roll-up | `pandas` | `_summary.json` / dashboard table |
| Config | `targets.yaml` + `pydantic` models | typed artifacts throughout |

Everything writes **JSON first, HTML second** — the HTML report is a rendering of the JSON so the human view and the agent view can never disagree.

---

## Part 7 — Guardrails

Non-negotiable defaults, encoded in the harness rather than left to discipline:

- **Identify honestly.** UA string naming the project with a contact URL. No impersonating Googlebot.
- **Respect `robots.txt` by default** (`--respect-robots` on). When robots forbids a path we want, *record the conflict in the report* and stop — the decision to seek permission is a human one. Note that L2 TLS impersonation is for header/TLS realism on allowed paths, not for defeating an explicit disallow.
- **Rate limit hard.** 1 concurrent request per host, 2–5s jittered delay, exponential backoff on 429/503, honor `Retry-After`. This is a survey, not a harvest — there is no reason to go fast.
- **Request budget per site: ~60.** Enough for the full pipeline, small enough to be invisible.
- **Never** solve CAPTCHAs, bypass authentication, use paid bypass services, or touch anything behind a login or paywall. If a site requires L4+, mark it `blocked` and move on — that *is* the finding.
- **Sample, don't mirror.** 10 detail pages per site, not 10,000. We are characterizing structure, not building the dataset.
- **No PII beyond publicly listed broker contact info**, which is published for exactly this purpose. Don't collect it from anywhere else.
- **Save the ToS** for every site so the legal picture is reviewable in one place.
- **Nothing in this repo depends on evading anything.** Sites that block us get recorded as blocked, with the observed vendor and tier — that's useful evidence too.

---

## Part 8 — Target list & tiers

Run in waves. Wave 1 validates the harness on maximum-diversity sites; the platform-family hypothesis then collapses much of Wave 2.

**Wave 1 — pilot (10 sites, one per archetype)**

| Site | Archetype being tested |
|---|---|
| landwatch.com | Large marketplace, likely CDN-defended, Land.com network |
| farmflip.com | LandFlip network, farm-specific |
| peoplescompany.com | Bespoke brokerage + auctions + map search |
| schraderauction.com | Auction-first, multi-tract, PDF-heavy |
| farmersnational.com | Large farm manager, auctions + private |
| hibid.com | Auction aggregator platform |
| acrevalue.com | Map/GIS-first, expect heavy internal API |
| properties.sc.egov.usda.gov | Legacy ASP.NET gov, `__VIEWSTATE` flow |
| nationalland.com | Regional brokerage, likely WordPress |
| steffesgroup.com | Regional auctioneer, likely white-label bidding platform |

**Wave 2 — marketplaces & national auction firms:** land.com, landsofamerica.com, landandfarm.com, landflip.com, ranchflip.com, landbrokermls.com, landsearch.com, farmsusa.com, farms.com, landleader.com, hertz.ag, halderman.com, bigiron.com, sullivanauctioneers.com, murraywiseassociates.com, ranchandfarmauctions.com, hallhall.com, buyafarm.com, ranchland.com, unitedcountry.com, farmlandpartners.com

**Wave 3 — regional firms (breadth test for the family hypothesis):** dreamdirt.com, stalcupag.com, pifers.com, gfarmland.com, midwestlandgroup.com, wheelerauctions.com, reckagri.com, greatplainslandcompany.com, kansaslandauction.com, kaufmanrealty.com, tuttland.com, afmrealestate.com, mossyoakproperties.com, whitetailproperties.com, fayranches.com, pearsonrealty.com

**Wave 4 — platforms, government, data, investment:** proxibid.com, auctionzip.com, bidwrangler.com, auctiontime.com, marknetalliance.com, realestatesales.gov, bid4assets.com, realauction.com, govease.com, auction.com, farmlandfinder.com, acres.com, id.land, landgate.com, acretrader.com, farmtogether.com

**Per-site scoring** (drives which sites the agent gets built against first):

```
accessibility  = 1 / fetch_tier          (L0=1.0 … L4=0.25, blocked=0)
data_quality   = has_api*3 + has_jsonld*2 + has_sitemap*1
field_coverage = fields_found / fields_in_ontology
volume         = log10(estimated_listing_count)
priority       = accessibility * (data_quality + field_coverage) * volume
```

---

## Part 9 — What the agent gets out of this

The corpus directly specifies the agent's tool surface. Each phase above is a tool:

`fetch_raw(url, tier)` · `render(url)` · `get_network_log()` · `read_robots()` · `list_sitemap()` · `extract_structured(url)` · `find_listing_cards(html)` · `probe_pagination(url)` · `discover_endpoints()` · `sample_detail_pages(n)` · `locate_field(field, page)` · `propose_recipe()` · `validate_recipe(recipe, holdout_urls)`

And the three build-time assets:

1. **`decision_tree.md`** — the prior the agent starts from, so it doesn't rediscover "check `__NEXT_DATA__` before rendering" on every site.
2. **`label_lexicon_global.json` + `value_format_grammar.md`** — the reason the agent handles "160 AC M/L" and "±160 acres" and "Acreage: 160" as the same field on a site it has never seen.
3. **Gold-labeled samples** — the eval set. Extraction accuracy per field per site, measurable, so recipe changes can be scored rather than eyeballed.

**Validation loop for a proposed recipe:** hold out 5 detail URLs not used during synthesis, run the recipe, compare against hand-filled `gold_value`s. Recipe ships only above a per-field threshold. This is the check that stops the agent from confidently generating selectors that matched one page and nothing else.

---

## Part 10 — Milestones

| # | Deliverable | Rough size |
|---|---|---|
| M1 | Harness skeleton: config, artifact writer, fetch ladder (L0–L2), P0–P2 running on 3 pilot sites | 1–2 days |
| M2 | Playwright integration: P3–P4, HAR capture, screenshots, hydration dumps | 1–2 days |
| M3 | P5 endpoint mining + standalone replay + bundle grepping | 1 day |
| M4 | P6 navigation mapping, URL taxonomy clustering, card detection | 1–2 days |
| M5 | P7–P8 detail sampling + field evidence + lexicon aggregation | 2 days |
| M6 | P10 reporting: `site_report.html`, `_index.html` dashboard, cross-site roll-ups | 1 day |
| M7 | Full run across Waves 1–4, triage blocked sites, write `decision_tree.md` | 2–3 days |

**Open questions to resolve during M1** (they change the schema, so answer them early):

1. **One row per listing page, or one row per tract?** Multi-tract auctions force this. Recommend: parent auction record + child tract records, with tracts optional.
2. **Do we keep sold listings?** Price history is valuable; storage and staleness are the cost. Recommend: keep, with a `status` transition log.
3. **How much do we invest in PDF parsing?** Brochures hold the best farmland data (FSA acres, CSR2, soil maps) but many are scanned images needing OCR. Recommend: capture PDFs in the evidence phase, decide on OCR after seeing the hit rate.
4. **Is the Land.com family really one backend?** If yes, Wave 2 shrinks substantially. Test in M1.
