# farmland

Evidence-gathering harness that profiles farmland listing websites: for each
site, how do you get from a base URL to listing pages, and how do you pull
fields off those pages. See `docs/evidence-gathering-plan.md` for the full
plan, `docs/build-prompts.md` for the build sequence, and `CLAUDE.md` for the
artifact layout and contracts.

## Run this locally, not in a sandboxed cloud environment

Sandboxed cloud dev containers block outbound traffic to non-allowlisted
hosts, so the harness cannot reach target sites from one. It also wants a
residential IP (datacenter ranges get scored harder by anti-bot vendors) and a
real desktop for Playwright. Run it on your own machine.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium   # one-time, ~150MB
evidence doctor                # preflight: python version, imports, chromium,
                                # DNS, outbound HTTPS, disk space, write perms
```

## Run

```bash
evidence list-sites
evidence run-wave 1
```

## Expect

- **Disk:** 5-20GB for a full 4-wave corpus. HARs, screenshots and PDFs
  dominate.
- **Wall clock:** 4-8 hours for all ~60 sites (60 requests/site x 2-5s
  jittered delay x 60 sites is ~4h of deliberate waiting, before render time).
  Wave 1 alone is roughly 40 minutes.
- **RAM:** 4GB+ free while the browser phases run.

By default the corpus is written to `./evidence` (gitignored — it never enters
git history). Point it elsewhere, e.g. an external drive, with:

```bash
evidence run-wave 1 --evidence-root /Volumes/scratch/farmland-evidence
```

Re-running is resumable: a phase whose result is already `ok` is skipped
unless you pass `--force`.
