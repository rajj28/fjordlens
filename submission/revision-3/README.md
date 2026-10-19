# FjordLens 1.2.0 — revision evidence, 20 October 2026

Final source: `7cb1a9492f847feb56b28336605137367cfee953` on `improve-signalpost-v3`.
The following artifact commit adds reports and documentation; it does not alter the runtime.
The 100-company smoke run used that exact source in a clean checkout and Python 3.11.9 virtual
environment, with no third-party runtime packages, model calls or search key.

## What changed

- Broader bounded discovery for registered trading names and acronym domains, and nested legal-page
  verification. Exact-company ownership checks remain mandatory.
- Optional independent-news research requires a fetched article and separately inspectable company,
  headline and publication-date evidence. Search results nominate URLs; snippets never become evidence.
- Contact/privacy/about/service pages, booking buttons, comments and hidden dates cannot become
  publications. Structured article subjects, titles and timestamps are checked against saved sources.
- Bounded recovery for transient source failures, correct independent-news export provenance, and
  diagnostics separating company coverage from the number of records.
- The final runtime reserves assembly time for large batches and includes exports/viewer generation
  in elapsed time. Earlier synthesis, comparison and citation-navigation fixes remain included.

## Reproduce

```bash
git checkout 7cb1a9492f847feb56b28336605137367cfee953
python -m venv .venv
# Activate the environment using the command for your shell.
python -m pip install -r requirements.txt
python -m fjordlens run --input companies.jsonl --output out/run
```

Builderr supplies `companies.jsonl`. Supply its actual limits using `--seconds` and `--max-requests`
or `SIGNALPOST_TIME_BUDGET_SECONDS` and `SIGNALPOST_MAX_REQUESTS`. Local defaults are 2,600 seconds,
20 requests per input with a 2,000 minimum, and 40 requests per company. The measured smoke used
600 seconds and 4,000 requests. `--registry-snapshot` and `--cutoff` support evaluator evidence controls.

## Verification

| Check | Exact-source smoke |
|---|---:|
| Input / terminal envelopes | 100 / 100 |
| Runtime including exports | 234.484 s |
| HTTP attempts | 1147 / 4000 |
| Failed execution envelopes / validation errors | 0 / 0 |
| Cited claims rechecked | 4588 |
| Raw financial values checked | 1842 |
| Citation references checked | 4665 |
| External API cost | $0 |

448 tests passed. Additional offline checks cover 1,100 inputs, repeated inputs, connector failures,
and forced deadline exhaustion. After the timing correction, the six-second deadline scenario wrote
all 1,100 envelopes in 4.625 seconds; unfinished research was marked failed. These synthetic checks
test output handling, not live research throughput or recall. Earlier failing deadline results are
retained in the verification bundle.

Desktop/mobile researcher tasks and 44 static/API answer comparisons passed, with no screenshot
errors or horizontal overflow. These were run on `025b287`; UI files are unchanged in the final source.
They are local checks, not Builderr's undisclosed six UX tasks.

## Observed coverage and limits

| Measure | Development baseline → final | Supplemental holdout baseline → candidate |
|---|---:|---:|
| Companies with verified websites | 8 → 11 | 8 → 9 |
| Companies with dated publications | 3 → 4 | 4 → 3 |
| Dated publication records | 44 → 38 | 24 → 18 |

The development batch informed the publication fixes and is not a holdout. The separate 100-company
holdout excludes all 300 earlier companies, seed 2026101704. Its candidate is `025b287`, before the
final timing-only changes; it was not used for tuning. Baselines ran on October 17 and candidates
later, so changes in public sources and network conditions confound causal comparisons.

The seven identified static-page/button false publications were excluded. Six dated Stolt portfolio
pages were also excluded because their CreativeWork timestamps sit outside an HTML article. This is
a real loss of historical portfolio coverage; their treatment and added-site identity checks are in
`verification/manual-review.json`. No official score, 80-point result, qualification or winning outcome is claimed.
No independent-news records were found in these keyless runs. Live search-enabled recall remains unmeasured.

## Submission requirements checked online

The [current contract](https://builderr.ai/docs/signalpost-evaluation-harness.md) requests a 100-company
smoke report and evaluates fresh, organizer-supplied batches currently numbering 1,000 and potentially
growing to 1,100. Its rubric is 50 recall, 30 evidence, 12 synthesis and 8 UX. An official run requires
65/100 and the integrity checks; local proxy scores cannot establish qualification. Revisions remain
open through November 7, with at most four revised hashes after the initial entry. Older local reference
files and cached public pages describe superseded requirements.

## Sources, cost and artifacts

Code: MIT. Runtime: Python standard library only. Sources: Brønnøysund open registry APIs (NLOD 2.0),
official annual accounts, the NAV public job feed under its terms, and permitted public company/news
pages with robots checks. Optional Brave Search requires an evaluator-supplied `BRAVE_SEARCH_API_KEY`;
results are transient. See [connector rights and limits](../../CRAWLERS.md). Measured keyless API cost is
$0; optional search shares the configured API-spend cap and is not required to execute the agent.
Contact: Ruturaj Sonkamble, ruturajsonkamble29@gmail.com.

- [Smoke run](smoke-100/run-report.json), [citation validation](smoke-100/citation-validation.json),
  [evidence audit](smoke-100/evidence-audit.json), [offline viewer](smoke-100/report/index.html).
- [Development comparison](comparisons/development-baseline-vs-final-smoke.json),
  [supplemental holdout comparison](comparisons/holdout-baseline-vs-approved-025b287.json),
  [verification](verification/verification.json), [manual evidence review](verification/manual-review.json).

The smoke artifact contains every cited response body and its metadata. Uncited/rejected pages and
the full holdout bundle remain outside the public artifact. Clone/download to open the offline viewer.

```bash
python scripts/validate_citations.py submission/revision-3/smoke-100
python scripts/audit_evidence.py submission/revision-3/smoke-100
python -m unittest discover -s tests -q
```
