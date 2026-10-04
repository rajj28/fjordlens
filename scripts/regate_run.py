"""Re-run the current identity gate on the pages a previous run saved, without network access.

    python scripts/regate_run.py out/run

For every website assessment in the run it rebuilds the gate context from the saved claims, parses the
saved snapshots of that site (homepage first), re-decides, and prints every decision that changed.
Use it after any gate change to prove no correct site was lost and every known-bad site is rejected.
"""
import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fjordlens import gate  # noqa: E402
from fjordlens.html import parse_html  # noqa: E402
from fjordlens.net import Response  # noqa: E402


def context_for(envelope):
    claims = envelope["claims"]
    value = lambda field: next((c["value"] for c in claims if c["field"] == field), None)  # noqa: E731
    entity = {"organisasjonsnummer": envelope["organisation_number"], "navn": envelope.get("legal_identity", {}).get("name") or "",
              "telefon": value("registered_phone"), "mobil": value("registered_mobile"), "epostadresse": value("registered_email"),
              "forretningsadresse": value("registered_address") or {}}
    workplaces = [c["value"]["address"] for c in claims if c["field"] == "registered_workplace" and isinstance(c["value"], dict) and isinstance(c["value"].get("address"), dict)]
    people = [c["value"]["name"] for c in claims if c["field"] == "registered_role" and isinstance(c["value"], dict)
              and c["value"].get("role_code") in {"DAGL", "LEDE", "INNH", "DTPR", "DTSO"} and isinstance(c["value"].get("name"), str)]
    return gate.Context(entity, workplaces, people)


def site_pages(folder, envelope, url):
    domain = gate.registered_domain(url)
    pages = []
    for snapshot in envelope["source_snapshots"]:
        if snapshot.get("http_status") != 200 or not snapshot.get("storage_path") or "html" not in (snapshot.get("content_type") or "text/html"):
            continue
        if gate.registered_domain(snapshot["final_url"]) != domain or snapshot["final_url"].endswith("robots.txt"):
            continue
        body = gzip.decompress((Path(folder) / snapshot["storage_path"]).read_bytes())
        response = Response(snapshot["final_url"], snapshot["final_url"], 200, body, {"content-type": snapshot.get("content_type") or "text/html"})
        page = parse_html(response.text(), snapshot["final_url"])
        if snapshot["final_url"].rstrip("/") == url.rstrip("/"):
            pages.insert(0, page)
        elif len(pages) < 3:
            pages.append(page)
    return pages[:3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    args = parser.parse_args()
    changed, checked = [], 0
    for line in (Path(args.folder) / "envelopes.jsonl").read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        ctx = context_for(envelope)
        for old in envelope["identity_assessments"]:
            pages = site_pages(args.folder, envelope, old["url"])
            if not pages:
                continue
            new = gate.assess(ctx, {"url": old["candidate_url"], "origin": old["origin"]}, pages)
            checked += 1
            if bool(new["publishable"]) != bool(old["publishable"]):
                changed.append((envelope["organisation_number"], ctx.name, old["url"], old["publishable"], new["publishable"], (new.get("rule") or "; ".join(new["reasons"]))[:90]))
    for row in changed:
        print(" | ".join(str(x) for x in row))
    print(f"{checked} assessments re-checked, {len(changed)} decisions changed")


if __name__ == "__main__":
    main()
