"""Self-contained report viewer written next to every run (works from file://, no server, no
third-party requests). Reuses the FjordLens web workspace (fjordlens/web) in static mode:

    report/index.html, app.js, style.css
    report/data/index.js        window.FJ_INDEX (one small row per company) + window.FJ_STATS
    report/data/chunk-NNNN.js   window.FJ_CHUNKS[n] = {org: compact profile} loaded on demand
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil

WEB = Path(__file__).resolve().parent / "web"
CHUNK_SIZE = 25


def _latest_amount(profile, field):
    items = [c for c in profile.get("claims", []) if c.get("field") == field and c.get("scope") == "legal_entity" and isinstance(c.get("value"), dict) and not c.get("stale")]
    items.sort(key=lambda c: (c.get("reporting_period") or {}).get("tilDato", ""), reverse=True)
    try:
        return float(items[0]["value"]["amount"]) if items else None
    except (KeyError, TypeError, ValueError):
        return None


def summary_row(profile, chunk=None):
    """List-view row; also used by the local server so both views agree."""
    values = {}
    for claim in profile.get("claims", []):
        values.setdefault(claim["field"], claim["value"])
    industry = values.get("industry")
    address = values.get("registered_address")
    row = {"organisation_number": profile["organisation_number"],
           "name": (profile.get("legal_identity") or {}).get("name") or "Identity unavailable",
           "industry": industry.get("beskrivelse", "Not reported") if isinstance(industry, dict) else "Not reported",
           "city": address.get("poststed", "Not reported") if isinstance(address, dict) else "Not reported",
           "employees": values.get("employees"), "claim_count": len(profile.get("claims", [])),
           "website_verified": (profile.get("availability", {}).get("website") or {}).get("state") == "available",
           "hiring": any(c["field"] == "job_posting" for c in profile.get("claims", [])),
           "state": profile.get("state"), "updated_at": (profile.get("run") or {}).get("completed_at"),
           "changes": sum(1 for c in profile.get("changes", []) if c.get("material")),
           "municipality": address.get("kommune") if isinstance(address, dict) else None,
           "legal_form": values.get("legal_form"),
           "industry_code": industry.get("kode") if isinstance(industry, dict) else None,
           "revenue": _latest_amount(profile, "financial_revenue"), "annual_result": _latest_amount(profile, "financial_net_profit")}
    if chunk is not None:
        row["chunk"] = chunk
    return row


def compact(profile):
    """Drop what the viewer never shows (raw snapshot metadata, crawl attempts) to keep chunks small."""
    keep = dict(profile)
    keep["source_snapshot_count"] = len(profile.get("source_snapshots", []))
    keep["source_snapshots"] = []
    keep["attempts"] = []
    keep["identity_assessments"] = [a for a in profile.get("identity_assessments", []) if a.get("publishable")]
    return keep


def _js(name, value):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"{name}={text};\n"


def write_report(envelopes, out_dir):
    out = Path(out_dir)
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in ("app.js", "screen.js", "style.css"):
        shutil.copyfile(WEB / name, out / name)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/style.css"', 'href="style.css"').replace('<script defer src="/app.js"></script>',
                                                                          '<script src="data/index.js"></script><script defer src="app.js"></script>')
    html = html.replace('<script defer src="/screen.js"></script>', '<script defer src="screen.js"></script>')
    html = html.replace('href="/api/export" download="fjordlens-profiles.json"', 'href="../envelopes.jsonl" download')
    html = html.replace('<a class="brand" href="/"', '<a class="brand" href="index.html"')
    (out / "index.html").write_text(html, encoding="utf-8")
    rows, chunks = [], {}
    unique = {}
    for profile in envelopes:
        unique.setdefault(profile["organisation_number"], profile)
    for position, profile in enumerate(unique.values()):
        chunk = position // CHUNK_SIZE
        chunks.setdefault(chunk, {})[profile["organisation_number"]] = compact(profile)
        rows.append(summary_row(profile, chunk))
    stats = {"profiles": len(rows), "claims": sum(r["claim_count"] for r in rows), "verified_sites": sum(r["website_verified"] for r in rows),
             "changes": sum(r["changes"] for r in rows), "retrieved_at": max((r["updated_at"] or "" for r in rows), default=None)}
    rows.sort(key=lambda r: (-r["website_verified"], -r["claim_count"], r["name"]))
    (data / "index.js").write_text(_js("window.FJ_INDEX", rows) + _js("window.FJ_STATS", stats), encoding="utf-8")
    for chunk, profiles in chunks.items():
        (data / f"chunk-{chunk:04d}.js").write_text("window.FJ_CHUNKS=window.FJ_CHUNKS||{};\n" + _js(f"window.FJ_CHUNKS[{chunk}]", profiles), encoding="utf-8")
    return {"report": str(out / "index.html"), "companies": len(rows), "chunks": len(chunks)}
