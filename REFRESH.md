# Refresh behavior (FjordLens v1)

## Invocation

- `--previous <envelopes.jsonl>` or `--previous <envelopes.jsonl.gz>`: compare with an earlier run. Only profiles for the same exact legal entity (identical canonical organisation number) can be compared.
- `--fresh`: ignore any prior run in the output folder; start clean.
- `--replay <bundle>`: serve all requests from a supplied source bundle (snapshots + metadata) without network access. Takes precedence over `--registry-snapshot` and `--previous`.
- `--registry-snapshot <folder>`: freezes only the exact official identity lookup (`/enhetsregisteret/api/enheter/<org>`) from a supplied bundle; other connectors run live. Missing supplied identity → abstention (not replaced with live fetch).
- Auto-archive: on a rerun into the same output folder without `--fresh` or `--previous`, `runner.archive_prior_output` moves the prior `envelopes.jsonl`, `run-report.json`, `manifest.jsonl`, `requests.jsonl` into `history/<run_id>/` and uses it as `--previous`.

## Semantic comparison (`refresh.refresh`)

Claims are keyed by stable `claim.id` (hash of org, family, field, key, scope, reporting period) — independent of extraction order, source retrieval timestamps, or content formatting.

**Semantic equality** normalizes:
- Object key order (sorted)
- Unordered collections (sorted by content hash)
- Financial amounts: decimal string normalized (trailing zeros removed, "0" canonicalized), `record_id` ignored
- Account type, currency, period must match

Account `record_id` alone does not create a business change.

## Change types

| Type | When | Material |
| --- | --- | --- |
| `changed_name`, `changed_legal_form`, `changed_address`, `changed_status` (bankrupt/liquidating), `changed_registry_website`, `changed_employee_count`, `changed_industry`, `changed_website`, `changed_role`, `changed_location`, `changed_financials`, `changed` | `semantic(old_value) != semantic(new_value)` for an existing claim; the type follows the field | Yes |
| `new_filing` | A filing year appears in the official filing-year claims, or a financial claim has a reporting period ending after every previous period; emitted **once per year**, never inferred from revenue, and not for a year the previous run already listed as filed (its figures are then `first_observed`) | Yes |
| `filing_value` | A further new figure of a filing already announced in this refresh | No |
| `new_role` / `new_location` | New official role or workplace when the official set was complete on both runs | Yes |
| `removed_role` / `removed_location` | Prior role or workplace absent, and the current official set is complete (`availability.complete == true`, state `available`/`not_available`) | Yes |
| `new_job` / `new_publication` | New hiring/activity claim dated after the previous run | Yes |
| `closed_job` | A NAV ad that was active is now marked not active by NAV's feed (live status, or the ad's own entry is inactive/gone); a failed or skipped check keeps the ad as a stale last-known value | Yes |
| `changed_employee_count` | Also when the previous run recorded "no registered employees" and a count now appears | Yes |
| `first_observed` | Any other new claim (our coverage grew; the source does not prove the fact is new) | **No** |

`refresh.changes_by_type` counts the change records by type.

Change record includes: `claim_id`, `field`, `family`, `type`, `material`, `previous_value`, `current_value`, `previous_evidence_ids`, `current_evidence_ids`, `current_source_snapshot_ids`, `observed_at`, and stable `id` (`ch_` + hash).

## Stale handling

Source failures (robots blocks, budget exhaustion, network errors, absent web items) **never** generate:
- "changed to unknown"
- False job closures
- False job reopenings

Instead:
- Prior supported claims are retained with `stale: true` and `stale_reason` set to the current `availability[family].reason`.
- `stale_claim_ids` are recorded in `refresh`.
- Prior last-observed timestamp is preserved.
- All prior evidence and source metadata remain referenced in `evidence` and `source_snapshots` (deep-copied from previous envelope if not already present).

Complete official roles/workplaces sets **can** establish removals (see `removed` above). Incomplete parses (failed source state) cannot.

## Evidence preservation (`runner.preserve_previous_snapshots`)

- Prior snapshot bodies (raw `<sha256>.<ext>`, or legacy `.bin.gz`) and metadata (`ss_*.json`) are copied from the previous run's `snapshots/` directory into the new output after SHA-256 verification.
- `snapshot_path` enforces package-local relative paths (no `..`, no absolute, no drive letters, no control chars, no backslashes).
- Required snapshots = those referenced by retained claims, changes (both sides), or current evidence.
- If a required snapshot body is missing, corrupt, or fails hash verification:
  - `snapshot.storage_path = null`, `previous_storage_path` recorded
  - `audit_availability.state = "unavailable"`, reason recorded
  - `errors` gets `{"family": "evidence_storage", "type": "snapshot_unavailable", ...}`
  - Envelope `run.terminal_status = "failed"`
- Unreferenced snapshots without saved bodies can remain as `metadata_only`.
- A public profile export alone is **not** a replacement for the raw source bundle.

## Cutoffs and timestamps

- `--cutoff <ISO datetime with timezone>`: live evidence retrieved after this instant is rejected (`Cutoff` error). Replay and previous-profile validation also enforce the cutoff.
- Cutoff and evidence `retrieved_at` must include a timezone (ISO 8601 with offset or `Z`). They are compared as UTC instants.
- A previous profile with evidence or a successful snapshot retrieved after the cutoff is rejected **before** new source collection.

## Idempotence

Replaying the same source bundle twice (same `--replay` folder) should yield:
- Identical stable claim IDs and values
- Zero material changes
- Execution timestamps and operational duration may differ (the project does not claim byte-identical full envelopes)

## Not a monitoring service

The agent checks sources when invoked. It is not a background monitoring service. Jobs and public activity should be refreshed more frequently in a hosted product; the challenge runner rechecks a company when it is included in a batch. A filing-year link is only a document-discovery record, not extracted historical financial data.