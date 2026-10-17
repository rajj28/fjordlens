# FjordLens version 2 — 17 October 2026

FjordLens 1.1.0 was run from a fresh checkout of source commit
`b6ead4fc9739ef6bc27666c88aed6f5ae6905dc8`. The artifact commit adds only reports and documentation.
Python 3.11.9, Windows 11, a clean virtual environment, no third-party runtime packages, no model calls,
and no search API key. The baseline is the previously submitted `95db0488a564830e12c018614f84a1e772f37fab`.

## Changes

- Direct cited answers, clearer business and financial summaries, comparable annual trends only when
  the captured periods/currencies/scopes support them, and no stale figures presented as current.
- Company selection and comparison open immediately on desktop/mobile; comparison citations open the
  correct company; historical changes expose both sides' evidence; static and API answers agree.
- Bounded recursive news/article discovery and nested sitemaps; stronger legal-page prioritization;
  separately verified company sections; sequential optional search fallback; unchanged root identity gates.
- Publication dates remain distinct from modification dates. Product catalogs, comment feeds, unsupported
  static-page dates and duplicate URLs are excluded. Original timestamps are retained when available.

## Clean-checkout smoke run

```bash
python -m venv .venv
# Activate the environment using the command for your shell.
python -m pip install -r requirements.txt
python -m fjordlens run --input submission/smoke-100/input-companies.jsonl --output out/run --seconds 600 --max-requests 4000 --fresh
```

| Measure | Public sample | Separate holdout |
|---|---:|---:|
| Inputs / terminal envelopes | 100 / 100 | 100 / 100 |
| Failed envelopes / evidence validation errors | 0 / 0 | 0 / 0 |
| Runtime | 150.422 s | 208.531 s |
| HTTP attempts | 994 / 4,000 | 927 / 4,000 |
| Published claims audited | 4,500 | 4,577 |
| Financial values checked against raw source | 1,842 | 1,889 |
| Citation references checked | 4,527 | 4,604 |
| Citation and evidence audits | Passed | Passed |
| Third-party API cost | $0 | $0 |

The holdout comprises 100 distinct companies sampled with seed 20261017 from the public 411,160-company
universe, excluding the public smoke sample. Its company evidence was not used to tune the implementation.
The final two runs were launched concurrently as separate processes; timing is specific to this machine
and network. Both completed within their individual 600-second budgets.

## Observed coverage, not an official score

| Measure | Public baseline → v2 | Holdout baseline → v2 |
|---|---:|---:|
| Companies with verified websites | 6 → 7 | 9 → 9 |
| Companies with dated publications | 3 → 4 | 2 → 2 |
| Dated publication records | 29 → 51 | 12 → 12 |

Other company-family coverage was unchanged. Supermercado's newly accepted website is supported by its
saved legal notice naming the exact organisation number. Publication records include company blogs,
marketing and food guides; the holdout publications date from 2012–2021. These are not all recent company
news and are not independently matched to Builderr's news pool. Baseline publication counts also contain
items that the revised precision filters now reject.

No official recall, accuracy, UX score, qualification or 80-point result is claimed. The heldout sample
does not demonstrate broader external recall improvement. Licensed search remains optional and its live
coverage has not been measured without evaluator-approved credentials. Live source changes can affect comparisons.

## Verification and artifacts

346 unit tests passed. Local desktop/mobile researcher tasks passed: find company, inspect evidence,
compare with correct citations, permalink navigation, direct answers, period/scope boundaries, and both
sides of change evidence. Static/API answers matched on 22 queries per viewport. Browser screenshots
showed no script errors or horizontal overflow. These checks are not Builderr's undisclosed six UX tasks.

- [Smoke run report](smoke-100/run-report.json), [citation validation](smoke-100/citation-validation.json),
  [evidence audit](smoke-100/evidence-audit.json), [offline viewer](smoke-100/report/index.html).
- [Public comparison](public-sample-comparison.json), [holdout comparison](holdout-comparison.json),
  [holdout run report](holdout-run-report.json), [holdout evidence audit](holdout-evidence-audit.json),
  [verification record](verification.json).

The public smoke artifact includes every body cited by a claim and its metadata, original envelopes,
observations, request receipts, inputs and viewer. Uncited/rejected pages and the raw holdout bundle are
kept outside the public artifact. Download or clone the repository to open the offline viewer; GitHub's
source preview does not execute the application.

```bash
python scripts/validate_citations.py submission/revision-2/smoke-100
python scripts/audit_evidence.py submission/revision-2/smoke-100
python -m unittest discover -s tests -q
```
