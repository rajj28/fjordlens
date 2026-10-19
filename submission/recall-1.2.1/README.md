# FjordLens 1.2.1 — measured recall and quota fixes

Runtime source: `02f5195453b7241728385d58966f399027576a5a` on `improve-signalpost-recall`.
The later artifact commit adds this report and evidence without changing the runtime.
The fresh 100-company smoke used the exact source in a clean Python 3.11.9 checkout.

## Changes

- Recover numeric social-profile IDs and trading handles declared in the verified company's own
  Organization markup. Ordinary page links still require a matching company/domain handle.
- Bind each declaration to the specific organization node. Distinct same-name siblings, conflicting
  identifiers, cross-site owner URLs and malformed social URLs are rejected. Each scalar/array value
  has an exact evidence selector, and the audit rechecks the binding and published URL.
- Limit Brave requests across the whole run, including website/news queries and retries. The default
  is six attempts; `--max-search-requests 0` disables them. The CLI overrides
  `FJORDLENS_MAX_SEARCH_REQUESTS`. This is a per-run safeguard, not a monthly account usage meter.
- Retain the earlier bounded discovery, independent-news evidence, synthesis, viewer, and batch deadline
  corrections described in [the 1.2.0 checkpoint](../revision-3/README.md).

## Reproduce

```bash
git checkout 02f5195453b7241728385d58966f399027576a5a
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -r requirements.txt
python -m fjordlens run --input companies.jsonl --output out/run --max-search-requests 0
```

Builderr supplies the evaluation input and actual limits. Use `--seconds` / `--max-requests` or
`SIGNALPOST_TIME_BUDGET_SECONDS` / `SIGNALPOST_MAX_REQUESTS`; `--registry-snapshot` and `--cutoff`
support evaluator evidence controls. This smoke used 600 seconds, 4,000 HTTP attempts and 40 attempts
per company. Its exact command and input hash are in [source provenance](smoke-100/source-provenance.json).
Optional search requires an evaluator-supplied key and an explicit agreed search allowance; no personal
credential is included. All measured smoke results below are keyless.

## Exact-source verification

| Check | Result |
|---|---:|
| Inputs / terminal envelopes | 100 / 100 |
| Runtime including exports | 234.75 s |
| HTTP attempts | 1144 / 4000 |
| Search attempts | 0 / 0 |
| Failed execution envelopes / validation errors | 0 / 0 |
| Cited claims rechecked | 4590 |
| Raw financial values checked | 1842 |
| Citation references checked | 4667 |
| External API cost | $0 |

All 470 tests passed. The smoke package includes every cited response body and re-runs its citation and
evidence audits after copying. Full offline replay outputs are omitted; their summaries are included.
Earlier desktop/mobile flows, 44 static/API answer comparisons and 1,100-input synthetic
deadline checks remain documented at the 1.2.0 checkpoint; they were not rerun for these backend changes
and are not the organizer's undisclosed UX tests.

## Observed coverage

| Measure on the same development sample | 1.2.0 live | 1.2.1 live |
|---|---:|---:|
| Companies with verified websites | 11 | 11 |
| Companies with social profiles | 6 | 7 |
| Social profile records | 10 | 12 |
| Companies with dated publications | 4 | 4 |
| Publication records | 38 | 38 |
| Independent-news records | 0 | 0 |

Live comparisons include network/source variation. The separate **frozen-snapshot re-extraction** replaces
old social claims before extracting them again, so it can detect losses. Development changed from
10 to 12 records and
6 to 7 companies, with
0 lost profiles. Supplemental holdout remained
4 records across 3 companies, with
0 losses. Both replay audits passed. Baseline capture commits and
the final extractor commit/hash are distinct in the [comparison metadata](offline-sameas/summary-v5.json).

The additions are Nettsmed's numeric LinkedIn URL and Elso Regnskap's numeric Facebook URL. They are
explicitly **declared by the company site**, with destination verification set to false. Restricted
platforms are not fetched. The stricter publication parser's earlier loss of six Stolt historical
portfolio pages remains documented; this release does not claim to recover them.

## Discovery experiment and remaining limitation

A targeted search pilot on three previously unresolved companies made six Brave API attempts, all HTTP
200, and found **zero additional verified websites**. Search was then stopped. This small selected pilot
is not a random recall benchmark; its $0.03 configured cost estimate is not an invoice. Only the
[experiment summary](search-pilot-review/experiment.json) and
[diagnosis](search-pilot-review/read-only-diagnosis.json) are included, without raw search responses.

In the preceding 1.2.0 run, 27 companies had no candidate site, 53 had no identity-verified candidate,
nine had fetch/access failures and 11 had verified sites. Review of 12 unresolved cases found no confirmed
ownership-gate false negative. A 100-company Wikidata official-site lookup returned no matches;
anonymous Norid reverse lookup was unsuitable under its access restrictions. Neither was shipped as a
speculative connector. Better permitted candidate discovery remains the largest unresolved opportunity.

No official score, 80-point result or qualification is claimed. At the reported recall score of 15.95,
even perfect scores in the other categories total only 65.95. Local coverage counts cannot predict the
organizer's labelled recall. The public company universe is available locally; hidden labels and the
official six UX tasks are not.

## Sources and artifacts

Python standard library only; no model calls. Code: MIT. Brønnøysund open registry data: NLOD 2.0;
NAV public job feed under its terms; permitted company/news pages with robots checks.
See [connector terms and limits](../../CRAWLERS.md) and the
[evaluation contract](https://builderr.ai/docs/signalpost-evaluation-harness.md).
Contact: Ruturaj Sonkamble, ruturajsonkamble29@gmail.com.

- [Run report](smoke-100/run-report.json), [citations](smoke-100/citation-validation.json),
  [evidence audit](smoke-100/evidence-audit.json), [offline viewer](smoke-100/report/index.html).
- [Verification](verification/verification.json), [live comparison](verification/live-coverage-comparison.json),
  [frozen social comparison](offline-sameas/summary-v5.json).

After checking out the artifact commit, validate the included bodies:

```bash
python scripts/validate_citations.py submission/recall-1.2.1/smoke-100
python scripts/audit_evidence.py submission/recall-1.2.1/smoke-100
python -m unittest discover -s tests -q
```
