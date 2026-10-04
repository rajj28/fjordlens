# Verification and measurement (FjordLens v1)

Do not infer a competition score from local tests. Only Builderr's checked reference pool can measure competitive recall, factual accuracy, and qualification.

## Local checks

The test suite covers:

- **Unit tests** (`tests/`):
  - Checksum validation, same-name collisions, longer-digit collisions, parent/group conflicts
  - Parking page detection, exact structured identity, all-factor corroboration
  - Wrong-company financial record rejection, decimal precision, missing vs zero
  - Company vs group accounting scope, missing reporting periods
  - Failed refresh retention, genuine diffs, idempotence
  - Mixed public/private DNS, unsafe URL forms, request ceilings
  - Replay integrity, robots blocking, malformed HTML parsing
  - Regression: conflicting structured identifiers, malformed JSON-LD types, owner-name substrings
  - Incomplete role lists, timezone-aware cutoffs
  - Preservation of historical snapshot bytes, missing/corrupt historical evidence
  - Invalid-input checkpoint paths
  - **Adversarial gate cases** (`tests/test_gate_v2.py`): sister-company name supersets, multi-entity sites, groupish owner strings, parked/placeholder pages, foreign .com guesses without org number, social/profile links, domain-placeholder titles, supplier credit org numbers, page-scope different entity, path-scoped sites, social handle filter

- **Evidence audit** (`scripts/audit_evidence.py`):
  - Opens actual saved response bytes from the private archive
  - Verifies content hashes and selector resolution against the live envelope
  - Checks structured legal-identity proof selectors against the target organisation number
  - Checks official financial amounts and qualifiers independently against raw JSON records
  - Pass rate = evidence integrity, not an estimate of exact-company precision

- **Live smoke runs**:
  - Small batches against live endpoints to verify connector wiring, budget pacing, and deadline behaviour
  - No correctness claims; only confirms the pipeline executes without crashes

- **Quote-kind verbatim rate**:
  - `evidence.quote_kind` distribution across envelopes: `verbatim_json_member` (official JSON), `verbatim_text` (HTML text span found in response), `normalized_text` (fallback)
  - Higher `verbatim_json_member`/`verbatim_text` ratio indicates tighter evidence grounding

- **Coverage per family in `run-report.json`**:
  - `family_company_coverage`: count of companies with `availability[family].state == "available"` per family
  - `published_claims`: total claim count
  - `evidence_validation_errors`: count of `validation_errors` across envelopes
  - `failed_envelopes`: envelopes with `run.terminal_status == "failed"`
  - `envelope_states`: distribution of terminal `state` values

## Explicitly unmeasured

- `official_score`: `null` in `run-report.json`
- `accuracy_and_recall`: "Not measured: requires independent checked labels"
- No calibrated confidence scores; `claim.confidence` is a heuristic publication label (1.0 official, 0.97 company-owned)

## Reports

`run_batch` writes `run-report.json` with:
- Requests, API spend, runtime, p50/p95 company runtime
- Terminal counts, source-family availability, claim counts, changes, errors
- Budget pass/fail (`budget.total <= max_requests`, `budget.cost <= 10`, `elapsed <= seconds`)
- Registry snapshot supplied, cache policy, Python version

Run `python scripts/finalize_submission.py` after collection to rebuild the public export from recorded evidence, audit the rebuilt batches, and exercise an unchanged refresh. The script creates the public reports and a private archive of raw snapshots and request receipts (`artifacts/fjordlens-private-evidence.zip`). Consult those generated reports for results; the procedure itself is not evidence that collection or validation has completed.