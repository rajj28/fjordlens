# Research agent (FjordLens v1)

The agent is a deadline-safe batch runner. Every input organisation number receives exactly one terminal envelope. The main thread is a watchdog: it never waits past the wall-clock budget, rewrites provisional outputs while work is in flight, and finalizes from consistent profile copies even if a worker hangs.

## Run state machine

Pipeline per company (see `runner.Pipeline`):

1. **Official stage** (`official_stage`): `official.identity` fetches the exact-org entity from `data.brreg.no`. On success, four parallel official-family fetches are submitted: `financials`, `leadership`, `locations`, `group`. The slow filing-history lane (`filings_stage`) is submitted to a single-worker executor. The website stage is submitted to the web pool.

2. **Website stage** (`website_stage`): `website.research` discovers candidate URLs (registry website, registry email domain, Wikidata, NAV employer homepage, DNS guess), fetches the homepage, runs the exact-entity identity gate (`gate.assess`), and if the gate passes, performs a bounded same-site crawl extracting description, contact, social profiles, hiring (JSON-LD JobPosting + careers links), and activity (JSON-LD articles + feeds).

3. **NAV job-feed catch-up** (`nav_refresh`): A background thread refreshes the shared NAV job listing index from the official feed (`jobs.NavJobs.refresh`). Companies whose official stage completed before the catch-up finishes are held until it signals ready; then the hiring stage (`hiring_stage`) is submitted.

4. **Hiring stage** (`hiring_stage`): `jobs.research` publishes active job ads whose official feed entry names the company's organisation number (or a registered workplace's number) as employer.

5. **Finalize** (`finalize`): At the deadline or when all work completes, each job's profile is snapshotted (`snapshot_profile`), operations are tallied, semantic refresh runs (`refresh.refresh`), the grounded summary is generated (`synthesis.summarize`), validation errors are collected (`core.validate`), and prior-run snapshots are preserved (`runner.preserve_previous_snapshots`). The envelope `state` is derived (`runner.envelope_state`).

## Thread pools

- `official`: up to 12 workers (official registry endpoints)
- `web`: configurable workers (company sites)
- `slow`: 1 worker (filing-history copies endpoint, rate-lane separated)
- `hiring`: up to 4 workers (NAV job-feed entry fetches)
- `nav_refresh`: single daemon thread (shared feed catch-up)

## Abstention policy

- **blocked**: Research requires an exact official identity anchor (`official_stage` sets non-identity families to `blocked` when `entity` is `None`).
- **ambiguous**: Identity gate found a candidate site but could not prove exact-entity ownership (`gate.assess` returns `decision="ambiguous"`).
- **not_available**: A family was researched but no supported claim was found (e.g., no job ads, no site contact details).
- **failed**: Connector error, validation error, deadline reached before the family was checked, or evidence storage failure during refresh.
- **not_applicable**: Registry explicitly reports the fact does not exist (e.g., `group` when `erIKonsern` is `false`).

A family is never marked `available` without at least one claim that has `evidence_ids` referencing a fetched, permitted source.

## Untrusted page text

Page text, HTML, JSON-LD, and feed content are treated as data only. They never execute tools, alter allowlists, change budgets, access secrets, or adjudicate identity. There is no model in the publication path: every published claim is produced by deterministic extractor code that validates its source against the frozen identity proof.

## No model in the publication path

All extraction, validation, gate decisions, refresh diffing, and summarization are pure Python functions with no external ML/LLM calls. The only network calls are to the documented official APIs and public company web pages.