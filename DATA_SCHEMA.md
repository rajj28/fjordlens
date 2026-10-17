# Data schema (FjordLens v1.1 behavior, envelope schema 1.0.0)

Each JSONL envelope is produced by `core.Profile` (during research) and `runner.finalize` (at terminal assembly).
The additions below preserve existing claim and evidence identifiers and the `1.0.0` envelope schema version.
Optional derived blocks may be unavailable if their assembly stage fails; errors and availability states record that outcome.

## Top-level envelope fields

| Field | Source | Description |
| --- | --- | --- |
| `schema_version` | `Profile.__init__` | Fixed string `"1.0.0"` |
| `organisation_number` | `Profile.__init__` / `runner.read_inputs` | Canonical 9-digit modulus-11 organisation number |
| `run` | `Profile.__init__` + `finalize.operations` | `{run_id, started_at, completed_at, terminal_status, deadline_reached}` |
| `legal_identity` | `official.identity` | `{organisation_number, name, legal_form, source_snapshot_id}` (may have `stale: true` after refresh) |
| `claims` | `Profile._add` | Array of claim objects (see Claim object) |
| `evidence` | `Profile._add` | Array of evidence objects (see Evidence object) |
| `source_snapshots` | `Profile._snapshot` | Array of snapshot metadata objects (see Snapshot object) |
| `availability` | `Profile._state` | Per-family state: `{state, reason, complete?, missing_fields?}` |
| `identity_assessments` | `website.research` | Array of `gate.assess` results for each candidate site tried |
| `attempts` | Various stages | Array of `{strategy, url, state, error?, snapshot_id?, ...}` recording each fetch attempt |
| `changes` | `refresh.refresh` | Array of change records (see Change object) |
| `errors` | `Profile._error`, `Job.failure`, `finalize` | Array of `{family, type, message, ...}` |
| `refresh` | `refresh.refresh` | `{previous_run_id, preserved_evidence[], stale_claim_ids[]}` |
| `operations` | `finalize.operations` | `{requests, runtime_ms, third_party_cost_usd}` |
| `summary` | `synthesis.summarize` | Grounded summary with `method`, `brief`, `sections[]`, `sentences[]`, `unknowns[]`, `change_count`, `material_change_count`, `stale_claim_count`, `refresh_note`, `interpretation` |
| `answers` | `answers.build_answers` | The same 11 standard questions for every company, in fixed order: `{question, answerable, answer, claim_ids, evidence_ids}`; answers use only cited claims, unknowns say why |
| `validation_errors` | `core.validate` | Array of strings; non-empty → `run.terminal_status="failed"` |
| `state` | `runner.envelope_state` | One of: `available`, `not_available`, `blocked`, `ambiguous`, `failed`, `not_applicable` |

## Claim object

| Field | Description |
| --- | --- |
| `id` | Stable hash: `cl_` + SHA-256 of `["claim-v1", org, family, field, key, scope, reporting_period]` |
| `field` | Claim field name (e.g., `financial_revenue`, `registered_role`, `job_posting`) |
| `family` | One of `FAMILIES` (identity, financials, leadership, locations, group, filing_history, website, description, contact, social_profiles, hiring, activity, workforce) |
| `key` | Discriminator within field (role code + name, workplace orgnr, fiscal period, publication URL, etc.) |
| `value` | Claim value (primitive, object, or array); money values are decimal strings with `currency` and `unit` |
| `availability` | Always `"available"` for published claims |
| `scope` | `legal_entity`, `consolidated_group`, `registered_subunit`, `relationship`, `site_declared_link`, `company_published_content`, etc. |
| `confidence` | `1.0` for official sources, `0.97` for company-owned sources |
| `evidence_ids` | Array of evidence `id` strings supporting this claim |
| `primary_evidence_id` | The evidence record that directly supports the claim; its provenance is copied onto the claim |
| `source_url`, `retrieved_at`, `content_sha256`, `claim_span`, `source_class`, `snapshot_path` | Claim-level provenance copied from the primary evidence record, so each claim is self-contained |
| `effective_at` | ISO date for roles, job postings, publications |
| `reporting_period` | `{fraDato, tilDato}` for financials |
| `first_observed_at` | Retrieval timestamp of first evidence |
| `last_observed_at` | Retrieval timestamp of latest evidence |
| `stale` | Boolean; set by `refresh` when a prior claim could not be re-confirmed |
| `last_changed_at` | Set by `refresh` on material change or first observation |
| `platform`, `signal_type` | Builderr observation labels on external claims (`observations.label_claims`): e.g. `company_site`/`company_profile` for the verified website, `job_board`/`job_posting` for NAV ads, `brreg`/`workforce_snapshot` for employee counts, `linkedin`/`profile_handle` for a declared profile |

### Family placement of registry and site fields

| Family | Fields |
| --- | --- |
| contact | `registered_phone`, `registered_mobile`, `registered_email` (entity), `workplace_phone`, `workplace_mobile`, `workplace_email` (each registered subunit, key `subunit_orgnr:value`), `website_email`, `website_phone`, `website_contact_address` (verified site only) |
| description | `registered_activity` (`/aktivitet`), `statutory_purpose` (`/vedtektsfestetFormaal`), both verbatim registry line arrays; `business_description` (verified site meta/about text) |
| locations | `registered_workplace`: `{organisation_number, name, address, industry, employees}` plus `phone`, `mobile`, `email`, `website` when the subunit registers them |
| website | `official_website` only after the identity gate accepts the site; `careers_page_url` (a careers link on the verified site, not a hiring fact); the registry `hjemmeside` stays an identity fact (`registry_website_candidate`) |
| workforce | `employees` (`/antallAnsatte`, effective at the Aa-registeret registration date), `employees_registered_on`, `workplace_employees` (per registered subunit); `not_available` with the reason when the registry holds no registered employees |
| identity (brand) | `public_brand` from the verified homepage (`og:site_name`, else the site's own Organization/WebSite `name`), labelled company-reported |
| hiring | `job_posting` only (NAV official feed items marked ACTIVE, or JobPosting markup on the verified site that has not expired; `posting_context: "employment_agency"` and a `note` when the employer is in NACE 78, because the position may be with a client). A careers-page link alone stays in the website family. |
| social_profiles | `company_linked_profile` from the verified website; `company_profile` when the entity's registry website field declares a social profile. Both retain their source class and declaration scope; the destination is not verified. |
| activity | `company_publication`: `{title, url, published_on, source_kind, independent_sentiment}` plus optional `published_at` when a source timestamp includes a time. `published_on` retains the source calendar date; `published_at` preserves the original ISO/RFC timestamp without inventing a missing timezone. The article URL can differ from the captured feed/listing URL in its evidence. |

## Summary and direct answers

`summary.method` is `grounded_templates_v3`. The brief contains `{text, claim_ids}`; sections contain
`{key, title, sentences}`; each sentence contains `{text, claim_ids, stale}`. Current business, leadership and
financial sections exclude stale claims. Last-known claims remain in the envelope and are explicitly described
as unconfirmed in the change section. `refresh_note` qualifies an unchanged result as applying to facts confirmed
in this run and reports earlier facts that could not be re-confirmed.

Financial statements use one reporting period per scope, with dates and company/group labels. A missing latest
figure is not backfilled from an older year. Calculated ratios cite their inputs. Annual changes require
consecutive full-year periods with matching calendar boundaries, scope and currency; the text names both periods
and cites both figures. A nonpositive prior value does not produce a percentage growth claim. Missing comparable
revenue produces an explicit limitation. Negative equity and registry distress flags are bounded observations,
not determinations of current solvency or cash flow.

The envelope's `answers` array keeps its existing eleven questions and shape. Interactive answers from
`research.answer_profile` (CLI) and `synthesis.answer` (server) use a common additive response:

| Field | Meaning |
| --- | --- |
| `organisation_number`, `company_name`, `question` | Company and requested question |
| `method` | `grounded_answers_v2` |
| `answer`, `answerable`, `abstained` | Direct readable answer; partial supported multi-topic answers can be answerable while still listing gaps |
| `topics` | Canonical question keys selected by the shared classifier |
| `sentences` | Answer segments `{text, claim_ids, stale}`; a segment can contain several related sentences |
| `claim_ids`, `evidence_ids` | Supporting identifiers; changes can cite earlier evidence without inventing a current claim |
| `citations` | `{claim_id, field, source_url, retrieved_at, content_sha256, quote, evidence_id}`; quotes are display excerpts of the evidence span |
| `claims`, `facts` | Supporting current claims and compact cited facts; facts also expose value, family, scope and reporting period |
| `availability`, `unsupported_or_uncertain` | Relevant availability records and explicit evidence gaps or unsupported intents |
| `changes`, `answer_policy` | Change records for change questions and the grounding policy |

Exact standard questions take precedence over regex intent routes. Unsupported prediction, sentiment and other
unmeasured requests abstain. Missing employees, hiring evidence or financials never become zero or a claim of
absence. The static report precomputes the same answers in `question_answers` and exports the shared route
configuration as `FJ_ANSWER_CONFIG`; these are viewer data additions, not new raw-envelope requirements.

## External observation export

`external-observations.jsonl` has one row per eligible current external claim with complete provenance.
`company_profile` and `company_linked_profile` both map to the declared platform's `profile_handle` signal.
Entity `employees` can produce `brreg/workforce_snapshot`; workplace counts are not exported as entity totals.

Existing observation identifiers and provenance remain intact. Additive fields are `field`, `family`, `scope`,
`value`, `evidence_ids`, `content_url`, `published_on`, `effective_at` and `reporting_period`. Publication metrics
retain title, direct URL and publication date; job metrics retain title, URL, publication/expiry values, status
and employer organisation number where present. The full `value` also preserves optional publication timestamps.
Missing values stay null.

`source_url` identifies the captured evidence, such as a feed or listing. `content_url` identifies the linked
article, job or official website; it must not be substituted for the evidence URL. `acquisition_mode` follows
the actual `source_class`: official registry/API and public NAV feed sources use `official_api`, while company
pages use `permitted_public_page`. A registry-declared social link therefore retains its official acquisition
mode even though its platform label is a social network. `identity_proof`, retrieval time, saved-body hash and
snapshot path remain available for verification.

## Evidence object

| Field | Description |
| --- | --- |
| `id` | `ev_` + SHA-256 of the evidence record |
| `snapshot_id` | `ss_` + 24-char hash of `[url, content_sha256, retrieved_at, status]` |
| `snapshot_path` | Relative path (inside the run folder) of the captured body; its SHA-256 equals `content_sha256` |
| `source_url` | Final URL after redirects |
| `source_class` | One of `SOURCE_CLASSES`: `official_registry`, `official_roles`, `official_subunits`, `official_annual_accounts`, `official_group_structure`, `company_owned`, `public_job_feed`, `third_party_publication` |
| `retrieved_at` | ISO timestamp of fetch |
| `content_sha256` | SHA-256 of decompressed response body (64 hex chars) |
| `extraction_method` | `json_pointer_v1`, `html_text_v1`, `html_contact_v1`, `html_href_v1`, `html_meta_description_v1`, `jsonld_jobposting_v1`, `jsonld_article_v1`, `jsonld_organization_v1`, `rss_atom_item_v1`, `identity_gate_v2`, `employer_orgnr_in_official_job_feed` |
| `selector` | `{"type": "json_pointer", "value": "/pointer"}` or `{"type": "parsed_html_pointer", "value": "/contacts/0"}` or `{"type": "normalized_text", "value": "verbatim window"}` |
| `claim_span` | Verbatim source bytes: JSON member (`"key":"value"`), HTML text span, or normalized text window (≤3000/4000 chars) |
| `quote_kind` | `verbatim_json_member`, `verbatim_text`, or `normalized_text` |
| `effective_at` | ISO date from source (role effective date, job posting date, etc.) |
| `reporting_period` | Financial reporting period object |
| `identity_proof` | For company-owned claims: `{method: "exact_org_endpoint", organisation_number}` or gate proof object |

## Snapshot object (source_snapshots)

| Field | Description |
| --- | --- |
| `id` | `ss_` + 24-char hash (same as evidence `snapshot_id`) |
| `requested_url` | Original URL requested |
| `final_url` | Final URL after redirects |
| `http_status` | HTTP status code (0 if error before response) |
| `retrieved_at` | ISO timestamp |
| `content_sha256` | SHA-256 of decompressed body |
| `redirect_chain` | Array of `{url, status, snapshot_id}` |
| `source_class` | As in evidence |
| `access_policy` | `official_open_api_NLOD_2.0`, `licensed_search_api`, `robots_checked_public_page`, `access_policy` (for robots.txt) |
| `storage_path` | Relative path to the raw body `snapshots/<sha256>.<ext>` (`html`, `json`, `xml`, `txt`, `bin`; legacy gzip `.bin.gz` files are still readable), or `null` if unavailable |
| `content_type` | Response `content-type` header |
| `last_modified` | Response `last-modified` header |
| `etag` | Response `etag` header |
| `body_size_bytes` | Decompressed body length |
| `cache_hit` | Boolean |
| `error` | Error message if fetch failed |
| `audit_availability` | Added by `preserve_previous_snapshots`: `{state: "available"|"unavailable"|"metadata_only", reason}` |

## Change object (in `changes`)

| Field | Description |
| --- | --- |
| `id` | `ch_` + SHA-256 of `[claim_id, kind, semantic(old), semantic(new), observed_at, prev_evidence_ids, curr_evidence_ids]` |
| `claim_id` | Claim ID |
| `field` | Claim field |
| `family` | Claim family |
| `type` | Typed by field: `changed_name`, `changed_legal_form`, `changed_address`, `changed_status`, `changed_registry_website`, `changed_employee_count`, `changed_industry`, `changed_website`, `changed_role`, `changed_location`, `changed_financials`, `changed`; `new_filing` (once per newly filed year), `filing_value`; `new_role`, `removed_role`, `new_location`, `removed_location`; `new_job`, `closed_job`, `new_publication`; `first_observed` |
| `material` | Boolean; `false` for `first_observed` (coverage growth) and `filing_value` (a further figure of an already announced filing) |
| `previous_value` / `current_value` | Claim values before/after |
| `previous_evidence_ids` / `current_evidence_ids` | Evidence IDs on each side |
| `current_source_snapshot_ids` | Snapshot IDs of 200-status sources in current run |
| `observed_at` | Timestamp of the change observation |

## Compact example envelope fragment

```json
{
  "schema_version": "1.0.0",
  "organisation_number": "987654321",
  "run": {
    "run_id": "fjordlens-20260101T120000Z",
    "started_at": "2026-01-01T12:00:00Z",
    "completed_at": "2026-01-01T12:05:30Z",
    "terminal_status": "completed",
    "deadline_reached": false
  },
  "legal_identity": {
    "organisation_number": "987654321",
    "name": "EXAMPLE AS",
    "legal_form": "AS",
    "source_snapshot_id": "ss_a1b2c3d4e5f6a7b8c9d0e1f2"
  },
  "claims": [
    {
      "id": "cl_a1b2c3d4e5f6a7b8c9d0e1f2",
      "field": "financial_revenue",
      "family": "financials",
      "key": "2023-01-01:2023-12-31:SELSKAP",
      "value": {"amount": "12345678", "currency": "NOK", "unit": "currency_units", "account_type": "SELSKAP", "record_id": "reg-123"},
      "availability": "available",
      "scope": "legal_entity",
      "confidence": 1.0,
      "evidence_ids": ["ev_b2c3d4e5f6a7b8c9d0e1f2a3"],
      "reporting_period": {"fraDato": "2023-01-01", "tilDato": "2023-12-31"},
      "first_observed_at": "2026-01-01T12:01:15Z",
      "last_observed_at": "2026-01-01T12:01:15Z",
      "stale": false
    }
  ],
  "evidence": [
    {
      "id": "ev_b2c3d4e5f6a7b8c9d0e1f2a3",
      "snapshot_id": "ss_a1b2c3d4e5f6a7b8c9d0e1f2",
      "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/987654321",
      "source_class": "official_annual_accounts",
      "retrieved_at": "2026-01-01T12:01:15Z",
      "content_sha256": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
      "extraction_method": "json_pointer_v1",
      "selector": {"type": "json_pointer", "value": "/0/resultatregnskapResultat/driftsresultat/driftsinntekter/sumDriftsinntekter"},
      "claim_span": "\"sumDriftsinntekter\":12345678",
      "quote_kind": "verbatim_json_member",
      "reporting_period": {"fraDato": "2023-01-01", "tilDato": "2023-12-31"},
      "identity_proof": {"method": "exact_org_endpoint", "organisation_number": "987654321"}
    }
  ],
  "source_snapshots": [
    {
      "id": "ss_a1b2c3d4e5f6a7b8c9d0e1f2",
      "requested_url": "https://data.brreg.no/regnskapsregisteret/regnskap/987654321",
      "final_url": "https://data.brreg.no/regnskapsregisteret/regnskap/987654321",
      "http_status": 200,
      "retrieved_at": "2026-01-01T12:01:15Z",
      "content_sha256": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
      "redirect_chain": [],
      "source_class": "official_annual_accounts",
      "access_policy": "official_open_api_NLOD_2.0",
      "storage_path": "snapshots/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2.json",
      "content_type": "application/json",
      "body_size_bytes": 45231,
      "cache_hit": false
    }
  ],
  "availability": {
    "financials": {"state": "available", "reason": "1 exact-company filed amounts; original currency and period retained"},
    "identity": {"state": "available", "reason": "Exact official identity retrieved"}
  },
  "identity_assessments": [],
  "attempts": [],
  "changes": [],
  "errors": [],
  "refresh": {"previous_run_id": null, "preserved_evidence": [], "stale_claim_ids": []},
  "operations": {"requests": 12, "runtime_ms": 328000, "third_party_cost_usd": 0.0},
  "summary": {"method": "grounded_templates_v3", "brief": {"text": "...", "claim_ids": []}, "sections": [], "sentences": [], "unknowns": [], "refresh_note": null},
  "validation_errors": [],
  "state": "available"
}
```
