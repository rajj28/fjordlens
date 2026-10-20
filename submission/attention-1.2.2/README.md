# FjordLens 1.2.2 release report

Runtime source: [`30b3eb4e9bc8dfaae1c798445683efc5adca4cb5`](https://github.com/rajj28/fjordlens/tree/30b3eb4e9bc8dfaae1c798445683efc5adca4cb5) on `improve-signalpost-recall`, from [the FjordLens repository](https://github.com/rajj28/fjordlens). This report is for that exact tested source. Runtime code uses the Python standard library and has no LLM dependency.

## What changed

1.2.2 adds a source-backed attention brief and answerable question suggestions shared by the offline and live viewers. Unsupported questions explain the evidence gap and suggest supported alternatives. Current facts take precedence over retained stale values in profiles, lists and screening; last-known facts remain explicitly labelled. Repeated overview context is collapsed while the complete summary remains in the exported data. Broader discovery, stricter article proof, exact-owned `sameAs` numeric IDs, and the six-attempt total optional-search cap were inherited from 1.2.1; 1.2.2 adds no new recall or extraction algorithm.

The attention brief distinguishes material changes from evidence categories: explicit official bankruptcy/liquidation flags; negative-equity indicators tied to their reporting periods; stale claims; and missing evidence. A negative-equity indicator is period-specific and is not itself an official bankruptcy/liquidation flag. Stale claims are last-known evidence, not current confirmation; absence of evidence is not evidence of absence. Consult the brief and its audits rather than inferring current status from an old claim.

## Validation and results

481 tests passed in a clean clone. Desktop and mobile Edge checks passed against both static and live interfaces, including 42 static/API query comparisons (21 per viewport). The exact-source 100-company live run and its audits completed successfully:

| Check | Verified result |
|---|---|
| Clean-clone regression suite | 481 tests passed |
| Live smoke inputs / terminal envelopes | 100 / 100 |
| Runtime / local budget | 240.157 s / 600 s |
| HTTP attempts / local allowance | 1,157 / 4,000 |
| Search requests / external API cost | 0 / $0 |
| Evidence audit | 4,590 claims; 1,842 raw financial values; zero audit errors |
| Citation contract | 4,667 citations; zero findings |
| Deadline-cut companies / envelope validation errors | 0 / 0 |
| Browser validation | Desktop/mobile static and live interfaces; 42 static/API answer comparisons |

The fresh run verified websites for 11/100 companies, social profiles for 7/100, and company-owned publications for 4/100. It captured 12 social-profile records and 38 company-owned publications, with no hiring or independently sourced news claims in this sample. These counts match the previous 1.2.1 smoke checkpoint; the new release does not demonstrate a recall increase. External discovery, jobs and independent news remain the main coverage gaps. The 100-company sample was reused for development; these are coverage counts, not recall against hidden ground-truth labels.

The input is the reused development smoke sample, not an unseen holdout and not an official score. Optional API search was explicitly disabled; no private credential was used. No official score, qualification, or 80-point outcome is claimed. The public challenge board at [builderr.ai/challenges/signalpost](https://builderr.ai/challenges/signalpost), checked 21 October IST, still showed the prior directional score of 50.74/100 (recall 15.95, evidence 29.99, synthesis 4.80, UX 0), reviewed 17 October. That is not a score for this release. The listed deadline is 7 November, next results are planned for 21 October, and the challenge allows four revisions; none of these facts imply qualification.

## Viewer-only scale check

Offline generation of a report from 1,000 saved provisional profiles completed in 9.984 seconds across 40 chunks, with browser interaction checks passing. This validates viewer generation and representative interactions only. It is not a live research benchmark or a completed 1,000-company run. The earlier live 1,000-company run was interrupted before a final report and contains a malformed request-log row; exclude it from completed research benchmarks. See [offline viewer-generation result](offline-1000-viewer.json) and [interrupted scale-run record](interrupted-scale-run.json).

## Reproduce and inspect

The fresh run used Python 3.11.9 in an empty virtual environment after the declared `python -m pip install -r requirements.txt` step. Its local limits were 600 seconds, 4,000 total HTTP attempts and 40 attempts per company. Use the organiser-provided limits for official runs. The machine-readable [validation summary](validation-summary.json), [run report](smoke-100/run-report.json), [evidence audit](smoke-100/evidence-audit.json) and [citation report](smoke-100/citation-validation.json) record the results.

From the release branch, rerun the smoke input with:

```bash
python -m fjordlens run --input submission/attention-1.2.2/smoke-100/input-companies.jsonl --output out/smoke-1.2.2 --seconds 600 --max-requests 4000 --per-company 40 --max-search-requests 0 --fresh
```

The evidence artifact is committed after the runtime source; keep the artifact checkout to inspect the saved files. The source hash above pins the runtime tested before packaging.

Viewer screenshots: [desktop](screens/desktop-company.png) and [mobile](screens/mobile-company.png).

The tested runtime can be checked out at the exact source above. No third-party packages are required by the runtime; the test suite can be run with the command below. The viewer regressions also use Node.js when installed:

```bash
python -m unittest discover -s tests
```

To inspect the saved smoke run, open [`smoke-100/report/index.html`](smoke-100/report/index.html) directly for the offline viewer. From the repository root, run the citation contract and evidence re-derivation checks with:

```bash
python scripts/validate_citations.py submission/attention-1.2.2/smoke-100
python scripts/audit_evidence.py submission/attention-1.2.2/smoke-100
```

The evidence package includes: evidence audits, [`source-provenance.json`](smoke-100/source-provenance.json), [`unit-tests.log`](unit-tests.log), browser logs, screenshots, saved source bytes, and [`artifact-manifest.json`](artifact-manifest.json). The fresh smoke path is [`smoke-100/`](smoke-100/). These artifacts are for audit and reproduction; do not treat the viewer-only scale check as live coverage.

## Assistance and scope

Copilot Auto assisted with development; verification was performed locally. OpenCode's free review timed out without a completed finding. No production-model dependency is used. Development-model calls are separate from the zero-cost, model-free runtime.

Browser reproduction (development tools only): install the pinned Playwright dependency from `package.json` with `npm install`, use a machine with Microsoft Edge, then run `python submission/attention-1.2.2/verification/browser-check.py . submission/attention-1.2.2/smoke-100/envelopes.jsonl out/browser-verification`. The helper regenerates the report offline and exercises static/live desktop and mobile interfaces.
