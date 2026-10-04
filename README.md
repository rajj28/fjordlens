# FjordLens

**Norwegian company intelligence where every fact is tied to the exact legal entity and a verbatim source.**
Give FjordLens organisation numbers; it returns one terminal profile per company with sourced facts, honest
availability states, a cited summary, change tracking against the previous run, and a self-contained report
viewer. Built for [Builderr's Signalpost challenge](https://builderr.ai/challenges/signalpost).

- **No API keys required, no model calls, no packages to install.** Python 3.10+ standard library only
  (tested on 3.11.9 and 3.14). One optional, evaluator-supplied key (Brave Search) adds website nomination;
  without it nothing paid is called.
- **Precision first.** A website, brand or contact is only published after an identity gate designed for zero
  wrong-company matches (organisation number in the site's owner position, or the full legal name plus
  independent registry corroboration). Uncertain matches are reported as `ambiguous`, never published.
- **Every claim is verifiable.** Source URL, retrieval time, SHA-256 of the saved response, reporting period
  where relevant, and a quote copied verbatim from the saved bytes (e.g. `"navn":"DIPS AS"`).

## Install and run (one command)

```bash
python -m pip install -r requirements.txt          # declared install step: no third-party packages
python -m fjordlens run --input companies.jsonl --output out/run
```

`--input` accepts the evaluator's file as JSONL (`{"organisation_number": "923609016"}` per line), a JSON array
or object, CSV/TSV with an `organisation_number`/`orgnr`/`organisasjonsnummer` column (quoted or not), or one
number per line. Every input row gets exactly one envelope, in input order, including invalid or duplicate rows.

`--output` may be a folder (`out/run`) or a result file (`out/result.jsonl` or `.json`); with a file name the
envelopes are written there and the artifacts go to a sibling `*-artifacts` folder. The process exits `0`
whenever every envelope was written; source failures are data, not process failures.

| Output | Contents |
|---|---|
| `envelopes.jsonl` | One terminal envelope per input row (schema in [DATA_SCHEMA.md](DATA_SCHEMA.md)) |
| `report/index.html` | Self-contained viewer: search, screen companies with an inspectable filter plan, compare up to 3 companies, open the source behind any fact; works offline from `file://` on desktop and mobile |
| `profiles/` | One readable JSON profile per company |
| `run-report.json` | Requests, runtime, p50/p95, per-family coverage, envelope states, limits used |
| `requests.jsonl` | Every HTTP attempt (redirects, robots.txt, retries included; search queries are not logged) |
| `external-observations.jsonl` | Builderr's observation view: one row per external claim with `platform` × `signal_type` (company_site, job_board, brreg, linkedin, …), identity proof and the claim's provenance |
| `snapshots/` | every captured response body byte-for-byte as `<sha256>.<ext>` (the file hashes to the evidence's `content_sha256`), plus metadata |

### Run budget

The agent always finishes inside the wall-clock budget and writes every envelope: requests stop at
`budget − margin`, a watchdog finalizes from consistent copies even if a source hangs, and provisional
`envelopes.jsonl` files are rewritten 15 s after start and then every 60 s while work is in flight.

| Setting | Flag | Environment variable | Default |
|---|---|---|---|
| Wall clock (s) | `--seconds` | `SIGNALPOST_TIME_BUDGET_SECONDS` | 2600 |
| Total HTTP attempts | `--max-requests` | `SIGNALPOST_MAX_REQUESTS` | 30 × companies (min 2000) |
| Attempts per company | `--per-company` | `FJORDLENS_PER_COMPANY` | 40 |
| Website workers | `--workers` | – | 32 |
| Optional search key | – | `BRAVE_SEARCH_API_KEY` | unset (search off) |
| API spend ceiling (USD) | – | `FJORDLENS_MAX_API_COST_USD` | 9.0 |

Every request also has a hard total deadline (twice the socket timeout, never past the run budget), so a server
that hangs or trickles bytes costs seconds, not the run; a company site that answers 429 with a long
`Retry-After` is skipped rather than waited out.

Measured on 300 random companies from the 411,160-company universe: about 8 HTTP attempts per company,
0 failed envelopes, all within budget. Third-party API cost is **$0**.

## What a profile contains

| Family | Source (all permitted, see [LIMITATIONS.md](LIMITATIONS.md)) |
|---|---|
| Legal identity and public brand | Brønnøysund Enhetsregisteret entity record (name, form, addresses, NACE, registers, registered website); the public brand the verified site uses for itself (`og:site_name` or its own Organization/WebSite markup) |
| Workforce | Registered employee count of the entity and of each registered workplace (with registration dates); "no registered employees" is a stated absence, never a zero |
| Financials | Regnskapsregisteret: latest filed company and consolidated accounts, exact decimals, currency, period, scope |
| Filing history | Official annual-account years with document links |
| Leadership | Official roles: CEO, board, auditor, accountant |
| Locations | Official registered workplaces (subunits) |
| Group | Official group structure |
| Website | Verified official website (identity gate v2) |
| Description | The registry's own activity and statutory-purpose text, plus the verified site's description |
| Contact | Registry phone, mobile and e-mail of the entity and of each registered workplace; e-mail/phone on the verified company domain; phone numbers the verified site's contact page labels as such |
| Social | Company profiles the verified site links, or lists as `sameAs` in its own organisation markup, whose handle carries the company or domain name |
| Hiring | NAV's official public job feed: active ads whose employer organisation number is the company or one of its registered workplaces (ads from staffing and recruitment agencies, NACE 78, are labelled as possibly for clients); JobPosting markup and careers pages on the verified site |
| Activity | Dated company publications from the verified site: RSS/Atom, article markup, news/blog listing pages (a link paired with the date shown next to it; the quote is the verbatim source of both), an article page's own publication date, and the site's WordPress posts API |

Each envelope also has a `summary` (brief plus sections: what it does, who leads it, where it operates, latest
filed numbers with labelled calculated ratios, hiring, recent activity, online presence, what changed, what is
unknown and why), where every sentence cites the claim IDs it rests on, and an `answers` block with the same
eleven standard questions for every company (what it does, who leads it, where it operates, latest filed
numbers, employees, website, hiring, what changed, recent activity, online presence, financial distress), each
answered only from cited claims or marked unanswerable with the reason.

## How identity is decided

Website candidates come from the registry website and business e-mail domain, the websites and e-mail domains
registered on the company's own subunits, NAV employer homepages and DNS-checked domains derived from the legal name;
with an evaluator-supplied search key, also from a web search (organisation number first, then legal name and town)
when no free candidate passes. Search results are never evidence: they are not stored, and a nominated site gets
no credit for being found. A site found by our organisation number can pass only by rule A (a supplier's reference
page also shows our number); a site found by name must carry the name in its domain and passes like a guessed
domain (rule A or D).
A candidate is accepted only by:

- **A** our organisation number in the site's owner position (footer or the site's own organisation markup), not
  inside a supplier credit; or
- **D** the site names itself as the company (title, site name, logo or © line; or the exact legal name in the text
  with no other companies around it) **and** shows the registered street address or the registered postcode
  written with its town; or
- **B / C / A2** a domain the company itself filed with the registry (entity or subunit website, business e-mail
  domain) whose owner strings carry the full legal name (B), or whose pages show the exact legal name with registry
  contact details and no group framing (C), or our organisation number with no other (A2).

These are the three kinds of proof Builderr's evaluation names: the organisation number on the page; the legal
name with the registered address; or a domain the company filed with the registry. A name alone, a name with a
phone number, or a name with a registered person is never enough for a domain we derived ourselves.

Longer legal names containing ours ("Bergen Bil Eiendom AS" for BERGEN BIL AS), group words, parked pages,
directories, other entities' organisation numbers, article subjects and agency credits all block acceptance.
Pages on a verified site that are titled after another legal entity are skipped. Details:
[IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md).

## Refresh and reruns

Running again into the same `--output` folder (or with `--previous old/envelopes.jsonl`) is a refresh: the old
run is archived under `history/`, unchanged facts keep their first-observed time, real differences become
typed change records citing both sides' evidence (`changed_name`, `changed_address`, `changed_status`,
`changed_employee_count`, `new_filing` once per newly filed year, `new_role`/`removed_role`,
`new_location`/`removed_location`, `new_job`/`closed_job`, `new_publication`, ...), and facts a failed source could not re-confirm are kept as
last-known values marked stale. A failure never erases evidence; replaying identical snapshots
(`--replay out/run`) produces no changes. Use `--fresh` to ignore an earlier run. See [REFRESH.md](REFRESH.md).

## Declared cache

`fjordlens/data/nav-index.json.gz` maps active NAV job ads to employer organisation numbers. It is built from
NAV's official public feed by `python scripts/build_nav_index.py`, only **nominates** ads, and is refreshed at
run time from the saved feed cursor; every published job is re-fetched live from the official feed and its
employer number re-checked. No other cache is used: all evidence is fetched live during the run.

## View and verify

```bash
# The run already wrote out/run/report/index.html: open it in any browser (no server needed).
python -m fjordlens serve --data out/run/envelopes.jsonl    # optional local server
python scripts/validate_citations.py out/run                # Builderr's citation contract: ids, URLs, times, snapshot bytes
python scripts/audit_evidence.py out/run                    # re-open saved bytes, re-check every claim and figure
python -m unittest discover -s tests                        # 267 tests incl. adversarial identity and hostile-server cases
```

## Ask and screen

Questions are answered from the saved evidence, never from a model's memory. Every returned fact carries its
source URL, retrieval time and SHA-256; missing evidence is reported as missing, never as zero.

```bash
# Cross-company screening: a closed grammar becomes an inspectable plan of explicit filters, or the agent abstains.
python -m fjordlens screen --data out/run/envelopes.jsonl "companies in Oslo with more than 5 employees top 10 by revenue"
python -m fjordlens screen --data out/run/envelopes.jsonl "legal form = AS with revenue above 25 million"
# One company: cited facts for financials, leadership, locations, hiring, activity or online presence.
python -m fjordlens ask --data out/run/envelopes.jsonl --org 915442552 "Who leads it and what are the latest numbers?"
```

Supported screen criteria: municipality, legal form, registered employees, latest filed revenue, profitable or
loss-making, has a verified website, has filed accounts, hiring, industry text, and `top N by revenue|employees`.
Queries about sentiment, reviews, web traffic, social buzz, fraud or "no website" are refused with the reason,
because no qualified source supports them. The same screening runs in the report viewer, with recent screens
kept in the browser and results exportable as JSON.

## Models, APIs, licences, cost

- Models: none at runtime.
- APIs: Brønnøysund Enhetsregisteret and Regnskapsregisteret (open data, NLOD 2.0); NAV job-vacancy feed
  (public token, [terms](https://arbeidsplassen.nav.no/vilkar-api)); company websites (including a verified site's
  own WordPress posts API), fetched only where robots.txt (RFC 9309) allows.
- Optional: Brave Web Search API, only if the evaluator sets `BRAVE_SEARCH_API_KEY`; results are used transiently
  to nominate candidate sites and are not stored. At most two queries for a company without a verified free
  candidate (in practice under 1.3 queries per company), i.e. about $6 per 1,000 companies at $5 per 1,000
  queries (`BRAVE_COST_PER_REQUEST_USD`), with a hard ceiling (`FJORDLENS_MAX_API_COST_USD`, default $9).
- No restricted platforms are fetched (LinkedIn, Facebook, Instagram, Google, Glassdoor, Indeed, X, TikTok);
  links to company profiles are recorded only as declared by the company's own site or registry entry.
- Credentials: none required. Expected third-party cost per official run: **$0** without the optional key.
- Code licence: see [LICENSE](LICENSE).

Design notes: [planning/PLAN.md](planning/PLAN.md) · Agent policy: [AGENT.md](AGENT.md) · Crawling:
[CRAWLERS.md](CRAWLERS.md) · Evaluation: [EVAL.md](EVAL.md)
