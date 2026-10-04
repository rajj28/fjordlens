# 100-company smoke test

`smoke-100/` is the result of one run of the submitted agent on Builderr's public 100-company sample
(`select_entry_batch.py` from the starter kit, default seed 20260823, over `signalpost-company-universe-2025.jsonl.gz`).
It was produced from a **fresh clone** of this repository at commit `f72ee90`, in an empty virtual environment, with
only the declared install step and the one run command:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # no third-party packages
.venv/Scripts/python -m fjordlens run --input smoke-companies.jsonl --output out/smoke-100
```

Machine: Windows 11, Python 3.11.9, home broadband; date 4 October 2026.

| Measure | Result |
|---|---|
| Inputs / terminal envelopes | 100 / 100 (every envelope `available`, 0 failed) |
| Wall clock | 122 s |
| HTTP requests (incl. robots.txt, redirects, retries) | 819 (8.2 per company) |
| Published claims | 4,473, every one with claim-level provenance |
| Citation contract (`scripts/validate_citations.py`) | valid: 4,496 citations, 0 findings (`citation-validation.json`) |
| Evidence audit (`scripts/audit_evidence.py`) | passed: 4,473 claims re-checked, 1,842 filed amounts matched to the raw source (`evidence-audit.json`) |
| Third-party API cost | $0 |

Contents: `envelopes.jsonl` (one envelope per input, input order), `run-report.json`, `requests.jsonl` (every HTTP
attempt), `manifest.jsonl`, `snapshots/` (every body cited by a claim's evidence, byte-for-byte as `<sha256>.<ext>`, plus metadata; uncited
pages such as rejected candidate sites and robots.txt files are left out of this public artifact),
`report/index.html` (the offline viewer: open it in a browser), and `input-companies.jsonl` (the 100 inputs).

Re-verify from this folder:

```bash
python scripts/validate_citations.py submission/smoke-100
python scripts/audit_evidence.py submission/smoke-100
```
