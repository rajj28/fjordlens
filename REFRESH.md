# Refresh behavior (FjordLens v0.2)

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
| `changed` | `semantic(old_value) != semantic(new_value)` for an existing claim | Yes |
| `removed` | Prior claim absent in current run, **and** family in `{leadership, locations}` **and** current `availability.state` in `{available, not_available}` **and** `availability.complete == true` | Yes |
| `first_observed` | New claim, family not in `{hiring, activity}` OR `effective_at` not after previous run's `completed_at` | **No** (coverage growth, not a business change) |
| `new_job` | New `hiring` claim with `effective_at` date after previous run's `completed_at` | Yes |
| `new_publication` | New `activity` claim with `effective_at` date after previous run's `completed_at` | Yes |

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

- Prior snapshot bodies (`.bin.gz`) and metadata (`.json`) are copied from the previous run's `snapshots/` directory into the new output after SHA-256 verification.
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