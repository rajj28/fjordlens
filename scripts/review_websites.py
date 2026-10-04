"""List every accepted website in a run with the rule and proof, for a human precision review.

    python scripts/review_websites.py out/run            # table on stdout
    python scripts/review_websites.py out/run --json     # machine-readable rows

Each row: organisation number, legal name, registered municipality, accepted URL, gate rule, candidate
origin, and the proof span (org-number window, owner string or person). Review every row before a
submission: one wrong site blocks qualification.
"""
import argparse
import json
from pathlib import Path


def rows(folder):
    path = Path(folder) / "envelopes.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        claims = envelope.get("claims", [])
        address = next((c["value"] for c in claims if c["field"] == "registered_address" and isinstance(c["value"], dict)), {})
        for assessment in envelope.get("identity_assessments", []):
            if assessment.get("publishable"):
                signals = assessment.get("signals", {})
                yield {"org": envelope["organisation_number"], "name": envelope.get("legal_identity", {}).get("name"),
                       "municipality": address.get("kommune") or address.get("poststed"), "url": assessment.get("url"),
                       "rule": (assessment.get("rule") or "")[:3].strip(": "), "origin": assessment.get("origin"),
                       "proof": (assessment.get("proof_span") or "")[:140],
                       "corroboration": [k for k in ("registry_phone_on_site", "registry_address_on_site", "registry_email_on_site",
                                                     "registered_person_on_site") if signals.get(k)]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    found = list(rows(args.folder))
    if args.json:
        print("\n".join(json.dumps(r, ensure_ascii=False) for r in found))
        return
    for r in found:
        print(f"{r['org']} | {str(r['name'])[:34]:34} | {str(r['municipality'])[:12]:12} | {r['rule']:2} | {r['origin'][:16]:16} | "
              f"{str(r['url'])[:44]:44} | {','.join(c.split('_')[1] for c in r['corroboration'])} | {r['proof'][:70]}")
    print(f"{len(found)} accepted websites")


if __name__ == "__main__":
    main()
