"""Check a run against Builderr's public citation contract (signalpost_public_citation_v1): byte linkage only.

    python scripts/validate_citations.py out/run            # envelopes.jsonl + snapshots/ under the run folder

For every envelope: unique evidence ids; every `available` claim cites at least one evidence id; every cited id
exists; cited source_url is http(s) without credentials; retrieved_at is ISO 8601 with a timezone; content_sha256 is
64 hex characters; snapshot_path is relative, stays inside the run folder, is a regular file of at most 20 MB, and the
SHA-256 of its bytes equals content_sha256. It does not check that a quote supports a value or a company
(see scripts/audit_evidence.py for that).
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

MAX_BYTES = 20 * 1024 * 1024


def check(run_dir, max_bytes=MAX_BYTES):
    root = Path(run_dir).resolve()
    findings, envelopes, citations = [], 0, 0
    hashes = {}

    def finding(line, org, claim_index, evidence_id, code, message):
        findings.append({"line": line, "organisation_number": org, "claim_index": claim_index, "evidence_id": evidence_id,
                         "code": code, "message": message})

    for line_number, line in enumerate((root / "envelopes.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        envelopes += 1
        envelope = json.loads(line)
        org = envelope.get("organisation_number")
        evidence, seen = {}, set()
        for record in envelope.get("evidence", []):
            if record.get("id") in seen:
                finding(line_number, org, None, record.get("id"), "duplicate_evidence_id", "Evidence id is not unique")
            seen.add(record.get("id"))
            evidence[record.get("id")] = record
        cited = set()
        for index, claim in enumerate(envelope.get("claims", [])):
            if claim.get("availability") == "available" and not claim.get("evidence_ids"):
                finding(line_number, org, index, None, "missing_evidence_reference", "Available claim cites no evidence")
            for evidence_id in claim.get("evidence_ids", []):
                if evidence_id not in evidence:
                    finding(line_number, org, index, evidence_id, "unknown_evidence_id", "Cited evidence record does not exist")
                    continue
                if evidence_id in cited:
                    continue
                cited.add(evidence_id)
                record = evidence[evidence_id]
                url = urlsplit(str(record.get("source_url") or ""))
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                    finding(line_number, org, index, evidence_id, "invalid_source_url", "source_url is not a credential-free http(s) URL")
                try:
                    stamp = datetime.fromisoformat(str(record.get("retrieved_at")).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        raise ValueError
                except ValueError:
                    finding(line_number, org, index, evidence_id, "invalid_retrieved_at", "retrieved_at is not ISO 8601 with a timezone")
                sha = str(record.get("content_sha256") or "")
                if not re.fullmatch(r"[0-9a-f]{64}", sha):
                    finding(line_number, org, index, evidence_id, "invalid_content_sha256", "content_sha256 is not 64 hex characters")
                    continue
                relative = record.get("snapshot_path")
                if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    finding(line_number, org, index, evidence_id, "invalid_snapshot_path", "snapshot_path is missing, absolute or escapes the run folder")
                    continue
                path = (root / relative).resolve()
                if root not in path.parents or not path.is_file() or path.is_symlink():
                    finding(line_number, org, index, evidence_id, "missing_snapshot", "snapshot_path is not a regular file inside the run folder")
                    continue
                if path.stat().st_size > max_bytes:
                    finding(line_number, org, index, evidence_id, "snapshot_too_large", "Snapshot exceeds the size limit")
                    continue
                if path not in hashes:
                    hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
                if hashes[path] != sha:
                    finding(line_number, org, index, evidence_id, "snapshot_hash_mismatch", "Captured bytes do not match content_sha256")
        citations += len(cited)
    findings.sort(key=lambda f: (f["line"], -1 if f["claim_index"] is None else f["claim_index"], f["evidence_id"] or "", f["code"]))
    return {"validator": "signalpost_public_citation_v1", "valid": not findings, "envelopes_checked": envelopes,
            "citations_checked": citations, "factual_support_verified": False, "entity_binding_verified": False,
            "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_dir")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = check(args.run_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    summary = {k: report[k] for k in ("valid", "envelopes_checked", "citations_checked")}
    summary["findings"] = len(report["findings"])
    summary["first_findings"] = report["findings"][:3]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
