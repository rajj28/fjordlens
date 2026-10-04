"""Semantic refresh: source failures never become business changes."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from .core import digest

def semantic(value):
    if isinstance(value, dict):
        result = {k: semantic(v) for k, v in value.items() if k not in {"record_id"}}
        if "amount" in result and "currency" in result:
            decimal = Decimal(str(result["amount"]))
            amount = format(decimal, "f")
            result["amount"] = amount.rstrip("0").rstrip(".") if "." in amount else amount
            if decimal == 0:
                result["amount"] = "0"
        return result
    if isinstance(value, list):
        return sorted((semantic(v) for v in value), key=lambda v: digest(v))
    return value

def refresh(current, previous=None, *, cutoff=None):
    def instant(value):
        if not isinstance(value, str):
            raise ValueError("Cutoff and evidence retrieval timestamps must be timezone-aware ISO dates")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid cutoff or evidence retrieval timestamp") from exc
        if parsed.utcoffset() is None:
            raise ValueError("Cutoff and evidence retrieval timestamps must include a timezone")
        return parsed.astimezone(timezone.utc)

    cutoff_at = instant(cutoff) if cutoff is not None else None
    if previous is None:
        for claim in current["claims"]:
            claim["last_changed_at"] = claim["first_observed_at"]
        return current
    if not previous or not previous.get("run"):
        raise ValueError("Previous profile is malformed; refusing to treat it as an initial run")
    if current["organisation_number"] != previous["organisation_number"]:
        raise ValueError("Refresh requires identical legal entities")
    if cutoff_at is not None:
        prior_sources = list(previous["evidence"]) + [
            s for s in previous["source_snapshots"] if s.get("http_status") == 200
        ]
        if any(instant(source.get("retrieved_at")) > cutoff_at for source in prior_sources):
            raise ValueError("Previous profile contains evidence retrieved after the supplied cutoff")
    current["refresh"]["previous_run_id"] = previous["run"]["run_id"]
    old = {c["id"]: c for c in previous["claims"]}
    new = {c["id"]: c for c in current["claims"]}
    def event(before, after, kind):
        claim = after or before
        old_value, new_value = (before or {}).get("value"), (after or {}).get("value")
        record = {"claim_id": claim["id"], "field": claim["field"], "family": claim["family"],
                  "type": kind, "material": True, "previous_value": old_value, "current_value": new_value,
                  "previous_evidence_ids": (before or {}).get("evidence_ids", []),
                  "current_evidence_ids": (after or {}).get("evidence_ids", []),
                  "current_source_snapshot_ids": [s["id"] for s in current["source_snapshots"] if s["http_status"] == 200],
                  "observed_at": (after or {}).get("last_observed_at") or max((s["retrieved_at"] for s in current["source_snapshots"] if s["http_status"] == 200), default=current["run"]["completed_at"])}
        record["id"] = "ch_" + digest([claim["id"], kind, semantic(old_value), semantic(new_value),
                                        record["observed_at"], record["previous_evidence_ids"], record["current_evidence_ids"]])[:24]
        current["changes"].append(record)
    for cid, claim in new.items():
        before = old.get(cid)
        if before:
            claim["first_observed_at"] = before["first_observed_at"]
            claim["last_changed_at"] = before.get("last_changed_at", before["first_observed_at"])
            if semantic(before["value"]) != semantic(claim["value"]):
                event(before, claim, "changed")
                claim["last_changed_at"] = claim["last_observed_at"]
        else:
            claim["last_changed_at"] = claim["first_observed_at"]
            # A fact seen for the first time is only a business change when the source itself dates it
            # after the previous run (a new job ad or publication). Otherwise our coverage grew; the
            # company did not change, so it is recorded as a non-material first observation.
            dated_after = (claim.get("family") in {"hiring", "activity"} and claim.get("effective_at")
                           and str(claim["effective_at"])[:10] > str(previous["run"].get("completed_at") or "")[:10])
            kind = {"hiring": "new_job", "activity": "new_publication"}.get(claim["family"]) if dated_after else "first_observed"
            event(None, claim, kind)
            if kind == "first_observed":
                current["changes"][-1]["material"] = False
    for cid, before in old.items():
        if cid in new:
            continue
        family = before["family"]
        state = current["availability"][family]
        # Only complete official role/workplace sets can prove a removal.
        if family in {"leadership", "locations"} and state.get("complete") and state["state"] in {"available", "not_available"}:
            event(before, None, "removed")
            continue
        preserved = deepcopy(before)
        preserved["stale"] = True
        preserved["stale_reason"] = state["reason"]
        current["claims"].append(preserved)
        current["refresh"]["stale_claim_ids"].append(cid)
    for field in ("evidence", "source_snapshots"):
        existing = {item["id"] for item in current[field]}
        current[field].extend(deepcopy(item) for item in previous[field] if item["id"] not in existing)
    current["refresh"]["preserved_evidence"] = [e["id"] for e in previous["evidence"]]
    if not current["legal_identity"] and previous.get("legal_identity"):
        current["legal_identity"] = {**previous["legal_identity"], "stale": True}
    return current
