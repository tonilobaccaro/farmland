# CLAUDE.md — Evidence-Gathering Harness

This repo builds `evidence/`, a per-site corpus of how farmland listing
websites can be scraped: fetch tiers, WAF posture, rendering model, data
interfaces, navigation topology, and field-level evidence on detail pages.
It is diagnostic instrumentation, not the scraper itself — see
`docs/evidence-gathering-plan.md` for the full plan and
`docs/build-prompts.md` for the prompt sequence that builds it.

## The one rule that matters

**Phases communicate ONLY through artifacts written to disk (via
`ArtifactWriter`), never in-memory.** A phase must be fully re-runnable in
isolation: given `evidence/<site>/`, it reads whatever earlier phases wrote,
does its work, and writes its own output. Nothing is passed between phases as
a Python object across a process boundary. This is what makes
`evidence run --site X --phases p8` work without re-running p1-p7, and what
lets a human re-run one phase after fixing a bug without burning the site's
request budget again.

## Directory layout

```
evidence/
├── _index.html                    # dashboard: every site, tier, WAF, API?, JS?, score
├── _summary.json                  # machine-readable roll-up
├── _cross_site/
│   ├── label_lexicon_global.json
│   ├── value_format_grammar.md
│   ├── platform_families.json
│   ├── endpoint_catalog.json
│   └── decision_tree.md
└── <site_slug>/
    ├── 00_meta/          run.json, dns_tls.json, headers.json, tech_fingerprint.json,
    │                     waf.json, fetch_ladder.json, blocked_report.json,
    │                     phases/<phase_name>.json  (one PhaseResult per phase, always)
    ├── 01_policy/        robots.txt, robots_parsed.json, terms_of_service.html,
    │                     sitemaps/{sitemap_index.xml,tree.json,url_patterns.json},
    │                     archive/  (Wayback/CommonCrawl snapshots, Prompt 1b)
    ├── 02_rendering/     home_raw.html, home_rendered.html, home_raw.png,
    │                     home_rendered.png, render_diff.json, hydration/*.json
    ├── 03_network/       har/{home,search,detail}.har, endpoints.json,
    │                     api_samples/*.json, graphql_introspection.json,
    │                     bundle_secrets.json
    ├── 04_navigation/    link_graph.json, url_taxonomy.json, search_form.json,
    │                     pagination_probe.json, geo_index_pages.json
    ├── 05_listing_pages/ search_p1_raw.html, search_p1_rendered.html,
    │                     search_p2_rendered.html, search_p1.png,
    │                     card_candidates.json, card_sample.html, card_container.html
    ├── 06_detail_pages/  sample_01/{raw.html,rendered.html,screenshot.png,jsonld.json,
    │                     microdata.json,opengraph.json,data_attrs.json,text.txt,
    │                     field_evidence.json,assets/*.pdf} … sample_10/
    ├── 07_fields/        label_lexicon.json, value_formats.json, field_coverage.md
    ├── 08_dynamics/      conditional_get.json, resample_diff.json
    └── 99_report/        site_report.md, site_report.html, scrape_recipe.json,
                           observations.md
```

`evidence/` is entirely gitignored. It will reach 5-20GB on a full run and
must never enter git history. Every phase and every artifact path above is
relative to `<evidence_root>/<site_slug>/` (default `evidence_root` is
`./evidence`, overridable with `--evidence-root`).

## Data contracts (`src/evidence/models.py`)

These are frozen once later prompts start depending on them. If a contract
turns out to be wrong on contact with a real site (expected during Prompt
1c's spike), fix it there, note the change and reason in
`docs/observations.md`, and do it early — the cost of a contract change grows
with every phase that already depends on it.

- **`FetchResult`** — the outcome of one HTTP(S) request at a given
  `FetchTier` (L0 httpx-default → L1 httpx-browser-headers → L2
  curl_cffi-impersonation → L3 playwright-headless → L4 playwright-headful →
  BLOCKED). Bodies are never held in the model — write them via
  `ArtifactWriter` and store `body_path`.
- **`PhaseResult`** — every phase's own outcome record. Written to
  `00_meta/phases/<phase>.json` after every run, success or failure. A phase
  that raises still gets recorded as `status: "failed"` with the exception in
  `error` — a phase must never let an exception propagate uncaught out of the
  CLI without leaving a `PhaseResult` behind, because a missing file there
  reads as "never ran," not "failed," and that's the wrong story.

## Config (`src/evidence/config.py`)

- **`SiteConfig`** — one row of `targets.yaml`: slug, base_url, wave (1-4),
  archetype, expected_family, optional notes/seed URLs. `archetype` and
  `expected_family` are hypotheses from `docs/evidence-gathering-plan.md`
  Part 3/8, not yet observed — correct them as phases produce evidence.
- **`RunConfig`** — run-wide knobs: `evidence_root`, `respect_robots`
  (default `True`, never flip this casually), `request_budget` (default 60),
  `min_delay_s`/`max_delay_s` (2.0/5.0), `concurrency` (1), `user_agent`
  (names the project + contact URL — never impersonate a browser's UA
  string), `timeout_s`.

## ArtifactWriter (`src/evidence/artifacts.py`)

The only way any phase touches disk. `ArtifactWriter(site_slug, root)` scopes
every write under `<root>/<site_slug>/`. JSON writes are always
`indent=2, sort_keys=True, ensure_ascii=False` so `git diff`-style comparisons
across re-runs stay readable (even though `evidence/` itself isn't committed,
this matters for eyeballing runs). Read a rel path back with `read_json` —
this is how phase N reads what phase N-1 wrote.

## Politeness (`src/evidence/politeness.py`)

`RateLimiter` (per-host jittered delay, exponential backoff on 429/503,
honors `Retry-After`), `RequestBudget` (hard cap per site, raises
`BudgetExhausted`), `RobotsGate` (wraps `protego`; when `respect_robots` is
True and a path is disallowed, it's recorded as a conflict and treated as not
allowed — never silently skipped, since the report must show what we chose
not to look at).

## How to add a phase

1. Create `src/evidence/phases/pN_name.py`.
2. Subclass `Phase` from `phases/base.py`, decorate with
   `@register("pN_name")`, set `depends_on = ["pM_other"]` if it reads
   artifacts another phase writes.
3. Implement `async def run(self, ctx: PhaseContext) -> PhaseResult`. Use
   `ctx.artifacts` for all I/O, `ctx.rate_limiter`/`ctx.budget`/`ctx.robots`
   for every outbound request. Never call an HTTP client directly without
   going through those three.
4. Add the module's dotted path to `PHASE_MODULES` in `src/evidence/cli.py`
   so `evidence run`/`run-wave`/`status` discover it.
5. A phase must handle "the site blocked us" as a normal, complete result —
   see "What success means" in `docs/build-prompts.md`. Never let a blocked
   site raise past the phase boundary.

## Guardrails (Part 7 of the plan — non-negotiable)

- Identify honestly: `RunConfig.user_agent` names the project and a contact
  URL. No impersonating Googlebot or a stock browser UA at L0/L1.
- `respect_robots` defaults `True`. A disallowed path is recorded as a
  conflict, not silently fetched anyway.
- Rate limit hard: 1 concurrent request per host, 2-5s jittered delay,
  exponential backoff on 429/503, honor `Retry-After`. This is a survey, not
  a harvest.
- Request budget ~60/site.
- **No CAPTCHA solving, no auth bypass, no paid bypass services, no proxies,
  no VPNs, no IP rotation, no `cf_clearance` harvesting, no stealth plugins
  beyond a normal browser** — anywhere in this codebase, ever. A site that
  needs any of those is recorded `blocked`; that record is the deliverable,
  not a failure.
- Sample, don't mirror: ~10 detail pages per site, not the whole catalog.
- Save the ToS for every site so the legal picture is reviewable in one
  place. Don't interpret it — just capture it.

## Fetch ladder and recon (`src/evidence/fetch.py`, `fingerprint/`, `recon/`, `blocked.py`)

Added in Prompt 1. `Fetcher` (in `fetch.py`) implements tiers L0 (httpx,
honest UA), L1 (httpx, full Chrome header set), L2 (curl_cffi, TLS
impersonation) — L3/L4 (Playwright) arrive in Prompt 2. `Fetcher.escalate()`
tries tiers in order and stops at the first response `looks_like_challenge()`
doesn't flag as a WAF interstitial; if every tier is blocked it returns the
last attempt's body (often the interstitial itself — useful evidence) tagged
`FetchTier.BLOCKED`. Every request goes through `RateLimiter` → `RequestBudget`
→ `RobotsGate`, always in that order, via `Fetcher._request`.

`fingerprint/waf.py` and `fingerprint/tech.py` are pure, no-network
classifiers: declarative rule tables scored against headers/cookies/body (WAF
vendor) or headers/cookies/html/script-srcs (framework/CMS). Every match
records which literal signal fired — never just a bare vendor/technology
name — so the JSON stays auditable. `recon/` holds the no-JS passive-recon
sources: `dns_tls.py` (DNS records + TLS cert, ASN lookup is a best-effort
stub pending an offline database), `robots.py` (protego wrapper + raw
Disallow-path extraction), `sitemap.py` (recursive sitemap-index walker,
network-injected via a `fetch_text` callback so the traversal/capping/lastmod
logic is unit-testable without a network), `legal.py` (ToS/Privacy link
scanner), `wellknown.py` (fixed well-known-path prober).

`blocked.py` builds `00_meta/blocked_report.json` from a phase's
per-page-class reachability — it's a pure function so the "open" /
"partial" / "blocked" classification and the `implication` heuristic are
unit-tested directly, independent of any live fetch.

`phases/p1_recon.py` and `phases/p2_static.py` are the first two real phases;
they orchestrate the above and are the reference examples for "how to add a
phase" above. Note `RobotsGate.reload()`: `PhaseContext.robots` is built
before any phase runs (from whatever `01_policy/robots.txt` already exists on
disk, if any), so p1_recon calls `ctx.robots.reload(robots_txt)` right after
fetching robots.txt for the first time — otherwise a fresh run's first phase
would fetch under a permissive gate for however long it takes to learn the
real policy.

## No-touch evidence sources (`src/evidence/passive/`, `phases/p1b_passive.py`)

Added in Prompt 1b. `passive/archive.py` (Wayback CDX + unrewritten `id_`
snapshots), `passive/commoncrawl.py` (recent-crawl index queries, NDJSON),
`passive/ctlogs.py` (crt.sh subdomain discovery — resolution only, never HTTP
probing), `passive/serp.py` (pluggable search-engine recon, off unless a real
backend+key is registered) and `passive/syndication.py` (footer-badge scan
against a known-aggregator list) are all pure parsing/classification
functions with the network call injected as a `fetch_text` callback — same
pattern as `recon/sitemap.py` — so they're unit-tested against canned
CDX/NDJSON/crt.sh JSON without touching a network.

`phases/p1b_passive.py` is the one phase in this codebase that talks to
*other* hosts on the target site's behalf instead of the target site itself.
Its `_infra_get` helper is rate-limited and budgeted like every other
request, but is deliberately **not** gated by `PhaseContext.robots` — that
gate holds the *target site's* robots.txt, which has nothing to do with
archive.org/crt.sh/commoncrawl.org policy (see the plan's Part B-bis: these
are "public infrastructure serving exactly this purpose"). `p1b_passive` has
`depends_on = []` on purpose and must never fetch the target site's own
origin directly — even its syndication-badge check only reads
`02_rendering/home_raw.html` if p2_static already wrote it, or falls back to
a Wayback snapshot, precisely so this phase stays safe to run first, last, or
on a site every other phase found fully blocked.

## Local setup

This runs on a real machine with a residential IP, not a server or a
sandboxed cloud dev container — see `docs/build-prompts.md` for why (sandboxed
containers cannot reach arbitrary outbound hosts). See `README.md` for the run
sequence.
