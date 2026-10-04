# FjordLens

**Norwegian company intelligence where every fact is tied to the exact legal entity and a verbatim source.**
Give FjordLens organisation numbers; it returns one terminal profile per company with sourced facts, honest
availability states, a cited summary, change tracking against the previous run, and a self-contained report
viewer. Built for [Builderr's Signalpost challenge](https://builderr.ai/challenges/signalpost).

- **No API keys, no paid services, no model calls, no packages to install.** Python 3.10+ standard library only
  (tested on 3.11.9 and 3.14).
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
| `requests.jsonl` | Every HTTP attempt (redirects, robots.txt, retries included) |
| `snapshots/` | gzip-compressed immutable response bodies plus metadata, referenced by every claim |

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
| Contact | Registry phone, mobile and e-mail of the entity and of each registered workplace; e-mail/phone on the verified company domain |
| Social | Company profiles the verified site links whose handle carries the company or domain name |
| Hiring | NAV's official public job feed: active ads whose employer organisation number is the company or one of its registered workplaces (ads from staffing and recruitment agencies, NACE 78, are labelled as possibly for clients); JobPosting markup and careers pages on the verified site |
| Activity | Dated company publications (RSS/Atom, article markup) from the verified site |

Each envelope also has a `summary` (brief plus sections: what it does, who leads it, where it operates, latest
filed numbers with labelled calculated ratios, hiring, recent activity, online presence, what changed, what is
unknown and why), where every sentence cites the claim IDs it rests on.

## How identity is decided

Website candidates come from the registry website and business e-mail domain, the websites and e-mail domains
registered on the company's own subunits, NAV employer homepages and DNS-checked domains derived from the legal name. A candidate is accepted only by:

- **A** our organisation number in the site's owner position (footer or the site's own organisation markup), not
  inside a supplier credit; or
- **B** a company-declared candidate (registry website or e-mail domain) whose owner strings (title, site name,
  logo, © line) carry the full legal name, on a site that shows no other legal entity; or
- **D** a name-derived domain with the full legal name **and** registry contact details or the registered
  CEO/chair/owner on the site; or
- **D3** a name-derived domain whose own title, site name, logo or © line writes our exact registered name with
  our legal form ("Arona Trehus AS"); registered names are unique in Norway; or
- **A2 / C** a company-declared site (registry or subunit website, e-mail domain, NAV employer homepage) that shows
  our exact legal name plus registry contact details, with our organisation number (A2) or without any group
  framing (C).

Longer legal names containing ours ("Bergen Bil Eiendom AS" for BERGEN BIL AS), group words, parked pages,
directories, other entities' organisation numbers, article subjects and agency credits all block acceptance.
Pages on a verified site that are titled after another legal entity are skipped. Details:
[IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md).

## Refresh and reruns

Running again into the same `--output` folder (or with `--previous old/envelopes.jsonl`) is a refresh: the old
run is archived under `history/`, unchanged facts keep their first-observed time, real differences become
typed change records citing both sides' evidence, and facts a failed source could not re-confirm are kept as
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
python scripts/audit_evidence.py out/run                    # re-open saved bytes, re-check every claim
python -m unittest discover -s tests                        # 142 tests incl. adversarial identity cases
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
  (public token, [terms](https://arbeidsplassen.nav.no/vilkar-api)); company websites, fetched only where
  robots.txt (RFC 9309) allows.
- No restricted platforms are fetched (LinkedIn, Facebook, Instagram, Google, Glassdoor, Indeed, X, TikTok);
  links to company profiles are recorded only as declared by the company's own site or registry entry.
- Credentials: none. Expected third-party cost per official run: **$0**.
- Code licence: see [LICENSE](LICENSE).

Design notes: [planning/PLAN.md](planning/PLAN.md) · Agent policy: [AGENT.md](AGENT.md) · Crawling:
[CRAWLERS.md](CRAWLERS.md) · Evaluation: [EVAL.md](EVAL.md)
