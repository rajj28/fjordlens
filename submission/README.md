# 100-company smoke test

`smoke-100/` is the result of one run of the submitted agent on Builderr's public 100-company sample
(`select_entry_batch.py` from the starter kit, default seed 20260823, over `signalpost-company-universe-2025.jsonl.gz`).
It was produced from a **fresh clone** of this repository at commit `0ad84d7` (FjordLens 1.0.0), in an empty virtual
environment, with only the declared install step and the one run command:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # no third-party packages
.venv/Scripts/python -m fjordlens run --input smoke-companies.jsonl --output out/smoke-100
```

Machine: Windows 11, Python 3.11.9, home broadband; date 5 October 2026. The 268 unit tests pass in the same clone.

| Measure | Result |
|---|---|
| Inputs / terminal envelopes | 100 / 100 (every envelope `available`, 0 failed) |
| Wall clock | 126 s |
| HTTP requests (incl. robots.txt, redirects, retries) | 838 (8.4 per company) |
| Published claims | 4,474, every one with claim-level provenance |
| Standard questions (`answers`) | 11 per company, each answered from cited claims or marked unanswerable with the reason |
| Builderr observation export | 55 rows in `external-observations.jsonl` (`platform` × `signal_type`) |
| Citation contract (`scripts/validate_citations.py`) | valid: 4,488 citations, 0 findings (`citation-validation.json`) |
| Evidence audit (`scripts/audit_evidence.py`) | passed: 4,474 claims re-derived from the saved bytes, 1,842 filed amounts matched to the raw source (`evidence-audit.json`) |
| Refresh (same command again into the same folder) | 100 / 100 compared with the archived first run: 0 changes, 0 stale claims, citation contract and audit still pass |
| Third-party API cost | $0 (the optional search key was not set) |

Contents: `envelopes.jsonl` (one envelope per input, input order), `run-report.json`, `requests.jsonl` (every HTTP
attempt), `manifest.jsonl`, `external-observations.jsonl`, `snapshots/` (every body cited by a claim's evidence,
byte-for-byte as `<sha256>.<ext>`, plus metadata; uncited pages such as rejected candidate sites and robots.txt files
are left out of this public artifact), `report/index.html` (the offline viewer: open it in a browser), and
`input-companies.jsonl` (the 100 inputs).

Re-verify from this folder:

```bash
python scripts/validate_citations.py submission/smoke-100
python scripts/audit_evidence.py submission/smoke-100
```
