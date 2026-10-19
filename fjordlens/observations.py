"""Builderr's external-observation view of our claims (platform × signal_type, as in the Signalpost starter kit).

`label_claims` stamps `platform` and `signal_type` onto external claims inside the envelope; `write_observations`
exports them as `external-observations.jsonl`, one observation per claim, carrying the claim's own provenance.
"""
from __future__ import annotations

import json

NETWORKS = {"linkedin": "linkedin", "facebook": "facebook", "instagram": "instagram", "twitter": "x", "x": "x",
            "youtube": "youtube", "tiktok": "tiktok"}
FIELDS = {
    "official_website": ("company_site", "company_profile"),
    "business_description": ("company_site", "company_profile"),
    "public_brand": ("company_site", "company_profile"),
    "company_linked_profile": (None, "profile_handle"),
    "company_profile": (None, "profile_handle"),
    "job_posting": (None, "job_posting"),
    "company_publication": ("company_site", "public_post"),
    "employees": ("brreg", "workforce_snapshot"),
}
REQUIRED = ("id", "primary_evidence_id", "source_url", "retrieved_at", "content_sha256", "claim_span", "source_class", "snapshot_path")


def platform_and_signal(claim):
    """(platform, signal_type) for an external claim, or None for registry facts and unknown networks."""
    mapping = FIELDS.get(claim.get("field"))
    if not mapping:
        return None
    platform, signal = mapping
    value = claim.get("value")
    if claim.get("field") in {"company_linked_profile", "company_profile"}:
        platform = NETWORKS.get(str(value.get("platform") or "").lower()) if isinstance(value, dict) else None
    elif claim.get("field") == "job_posting":
        platform = ("job_board" if "NAV" in str(value.get("source") or "") else "company_site") if isinstance(value, dict) else None
    elif claim.get("field") == "company_publication" and claim.get("source_class") == "third_party_publication":
        platform, signal = "news", "public_mention"
    return (platform, signal) if platform else None


def label_claims(envelope):
    for claim in envelope.get("claims", []):
        labels = platform_and_signal(claim)
        if labels:
            claim["platform"], claim["signal_type"] = labels
    return envelope


def _metrics(field, value):
    if field == "official_website":
        return {"url": value} if isinstance(value, str) else {}
    if not isinstance(value, (dict, int, str)) or isinstance(value, bool):
        return {}
    if field in {"company_linked_profile", "company_profile"}:
        return {"url": value.get("url")} if isinstance(value, dict) else {}
    if field == "job_posting":
        return {k: value.get(k) for k in ("title", "url", "published", "expires", "status", "employer_orgnr")} if isinstance(value, dict) else {}
    if field == "company_publication":
        return {k: value.get(k) for k in ("title", "url", "published_on", "published_at")} if isinstance(value, dict) else {}
    if field == "employees":
        return {"employees": value} if isinstance(value, int) else {}
    return {"text": value[:300]} if isinstance(value, str) else {}


def to_observations(envelope):
    org = envelope.get("organisation_number")
    evidence = {e.get("id"): e for e in envelope.get("evidence", [])}
    observations = []
    for claim in envelope.get("claims", []):
        if claim.get("availability") != "available" or claim.get("stale") or not all(claim.get(k) for k in REQUIRED):
            continue
        labels = platform_and_signal(claim)
        if not labels:
            continue
        platform, signal = labels
        primary = evidence.get(claim["primary_evidence_id"], {})
        proof = primary.get("identity_proof") or {}
        if claim["source_class"] == "third_party_publication" and proof.get("method"):
            identity = {"type": str(proof["method"]), "organisation_number": org}
            identity.update({key: proof[key] for key in ("rule", "article_url", "evidence_span", "registry_snapshot_id")
                             if key in proof})
        elif proof.get("method") == "identity_gate_v2" or proof.get("rule"):
            identity = {"type": "website_identity_gate", "status": "exact", "rule": str(proof.get("rule") or "")[:200]}
        elif proof.get("method"):
            identity = {"type": str(proof["method"]), "organisation_number": org}
        else:
            identity = {"type": "exact_org_endpoint", "organisation_number": org}
        value = claim.get("value")
        content_url = value.get("url") if isinstance(value, dict) else value if claim.get("field") == "official_website" else None
        published_on = (value.get("published_on") or value.get("published")) if isinstance(value, dict) else None
        observations.append({
            "id": "obs-" + claim["id"], "claim_id": claim["id"], "organisation_number": org,
            "field": claim["field"], "family": claim.get("family"), "scope": claim.get("scope"),
            "value": value, "evidence_ids": list(claim.get("evidence_ids", [])),
            "platform": platform, "signal_type": signal,
            "source_url": claim["source_url"], "retrieved_at": claim["retrieved_at"], "content_sha256": claim["content_sha256"],
            "source_class": claim["source_class"], "evidence_span": claim["claim_span"], "snapshot_path": claim["snapshot_path"],
            "exact_entity": True, "identity_proof": [identity],
            "acquisition_mode": "official_api" if claim["source_class"].startswith("official") or claim["source_class"] == "public_job_feed" else "permitted_public_page",
            "rights_status": "approved", "strategy": primary.get("extraction_method") or "unknown",
            # A feed/listing is the captured evidence; the linked article/job is the subject.
            # Keep both instead of falsely claiming to have fetched the subject URL.
            "content_url": content_url, "published_on": published_on,
            "published_at": value.get("published_at") if isinstance(value, dict) else None,
            "effective_at": claim.get("effective_at"), "reporting_period": claim.get("reporting_period"),
            "metrics": _metrics(claim.get("field"), claim.get("value")),
        })
        if claim["source_class"] == "third_party_publication":
            observations[-1]["supporting_evidence"] = [
                {"evidence_id": eid, **{key: evidence[eid].get(key) for key in
                 ("source_url", "retrieved_at", "content_sha256", "snapshot_path", "claim_span", "selector")}}
                for eid in claim.get("evidence_ids", []) if eid in evidence]
    return observations


def write_observations(envelopes, path):
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        for envelope in envelopes:
            for observation in to_observations(envelope):
                stream.write(json.dumps(observation, ensure_ascii=False) + "\n")
                count += 1
    return count
