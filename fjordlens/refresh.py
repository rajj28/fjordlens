"""Semantic refresh: source failures never become business changes; real changes are typed.

A refresh compares this run's claims with the previous run's by stable claim id and emits typed change records
(changed_name, new_filing, new_role, closed_job, ...). Rules:
- A changed value of the same fact is a material change of a type chosen by its field.
- A new filing is detected from the official filing years or a newer reporting period, never from revenue alone,
  and one filing yields one change.
- A fact seen for the first time is only a business change when the source proves it is new: a complete official
  role or workplace set on both runs, a job ad or publication dated after the previous run, or a newer period.
  Otherwise our coverage grew; that is a non-material first observation.
- A missing fact is only a removal when a complete check proves it (complete official sets; NAV's live feed for job
  ads). Otherwise the last known value is kept, marked stale, with the reason.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from .core import digest

# Changed value of the same fact → typed change.
CHANGE_TYPES = {
    "legal_name": "changed_name", "legal_form": "changed_legal_form",
    "registered_address": "changed_address", "postal_address": "changed_address",
    "bankrupt": "changed_status", "liquidating": "changed_status",
    "registry_website_candidate": "changed_registry_website",
    "employees": "changed_employee_count", "workplace_employees": "changed_employee_count",
    "industry": "changed_industry", "secondary_industry": "changed_industry", "tertiary_industry": "changed_industry",
    "official_website": "changed_website", "registered_role": "changed_role", "registered_workplace": "changed_location",
}
FILING_FIELDS = {"latest_filing_year", "available_filing_years"}
NEW_TYPES = {"registered_role": "new_role", "registered_workplace": "new_location"}
REMOVED_TYPES = {"registered_role": "removed_role", "registered_workplace": "removed_location"}
NON_MATERIAL = {"first_observed", "filing_value"}


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


def change_type(field, family):
    if field in CHANGE_TYPES:
        return CHANGE_TYPES[field]
    return "changed_financials" if family == "financials" else "changed"


def filing_years(value):
    """Years named by a filing-year claim value ("2025" or ["2025", "2024"])."""
    values = value if isinstance(value, list) else [value]
    return {str(v) for v in values if str(v).isdigit() and len(str(v)) == 4}


def nav_confirmed_inactive(profile):
    """Job-ad ids that NAV's official feed showed as no longer active in this run (positive evidence only)."""
    return {uuid for a in profile.get("attempts", []) if a.get("strategy") == "nav_job_feed_v1"
            for uuid in a.get("confirmed_inactive") or []}


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
    previous_run_day = str(previous["run"].get("completed_at") or "")[:10]

    def event(before, after, kind, **extra):
        claim = after or before
        old_value, new_value = (before or {}).get("value"), (after or {}).get("value")
        record = {"claim_id": claim["id"], "field": claim["field"], "family": claim["family"],
                  "type": kind, "material": kind not in NON_MATERIAL, "previous_value": old_value, "current_value": new_value,
                  "previous_evidence_ids": (before or {}).get("evidence_ids", []),
                  "current_evidence_ids": (after or {}).get("evidence_ids", []),
                  "current_source_snapshot_ids": [s["id"] for s in current["source_snapshots"] if s["http_status"] == 200],
                  "observed_at": (after or {}).get("last_observed_at") or max((s["retrieved_at"] for s in current["source_snapshots"] if s["http_status"] == 200), default=current["run"]["completed_at"]),
                  **extra}
        record["id"] = "ch_" + digest([claim["id"], kind, semantic(old_value), semantic(new_value),
                                        record["observed_at"], record["previous_evidence_ids"], record["current_evidence_ids"]])[:24]
        current["changes"].append(record)

    def complete_on_both(family):
        before, after = previous.get("availability", {}).get(family, {}), current["availability"].get(family, {})
        return (bool(before.get("complete")) and bool(after.get("complete"))
                and before.get("state") in {"available", "not_available"} and after.get("state") in {"available", "not_available"})

    # Pass 1: facts present on both runs. Filing-year claims announce new filings first, so a filing is reported once.
    announced_years = set()
    for cid, claim in new.items():
        before = old.get(cid)
        if not before:
            continue
        claim["first_observed_at"] = before["first_observed_at"]
        claim["last_changed_at"] = before.get("last_changed_at", before["first_observed_at"])
        if semantic(before["value"]) == semantic(claim["value"]):
            continue
        claim["last_changed_at"] = claim["last_observed_at"]
        if claim["field"] in FILING_FIELDS:
            added = sorted(filing_years(claim["value"]) - filing_years(before["value"]))
            fresh = [y for y in added if y not in announced_years]
            if added and fresh:
                announced_years.update(fresh)
                event(before, claim, "new_filing", filing_years=fresh)
            elif not added:
                event(before, claim, "changed")
            continue
        event(before, claim, change_type(claim["field"], claim["family"]))

    # Pass 2: facts seen for the first time.
    previous_period_end = max((str((c.get("reporting_period") or {}).get("tilDato") or "") for c in previous["claims"]
                               if c.get("family") == "financials"), default="")
    previous_workforce_reason = str(previous.get("availability", {}).get("workforce", {}).get("reason") or "")
    # Filing years the previous run already knew from the official filing list: their figures are not new filings.
    known_years = set()
    for c in previous["claims"]:
        if c.get("field") in FILING_FIELDS:
            known_years |= filing_years(c.get("value"))
    for cid, claim in new.items():
        if cid in old:
            continue
        claim["last_changed_at"] = claim["first_observed_at"]
        family, field = claim.get("family"), claim.get("field")
        kind = "first_observed"
        if family == "financials" and claim.get("reporting_period"):
            period_end = str(claim["reporting_period"].get("tilDato") or "")
            if previous_period_end and period_end > previous_period_end:
                year = period_end[:4]
                if year in known_years and year not in announced_years:
                    kind = "first_observed"  # coverage recovered: the filing itself was already known
                elif year not in announced_years:
                    announced_years.add(year)
                    kind = "new_filing"
                else:
                    kind = "filing_value"
        elif field in NEW_TYPES and complete_on_both(family):
            kind = NEW_TYPES[field]
        elif family in {"hiring", "activity"} and claim.get("effective_at") and str(claim["effective_at"])[:10] > previous_run_day:
            kind = "new_job" if family == "hiring" else "new_publication"
        elif field == "employees" and "harRegistrertAntallAnsatte=false" in previous_workforce_reason:
            kind = "changed_employee_count"
        extra = {"filing_years": [str(claim["reporting_period"]["tilDato"])[:4]]} if kind == "new_filing" else {}
        event(None, claim, kind, **extra)

    # Pass 3: facts that are gone. Only a complete check proves a removal; otherwise keep the last known value.
    inactive = nav_confirmed_inactive(current)
    for cid, before in old.items():
        if cid in new:
            continue
        family, field = before["family"], before["field"]
        state = current["availability"][family]
        if field in REMOVED_TYPES and state.get("complete") and state["state"] in {"available", "not_available"}:
            event(before, None, REMOVED_TYPES[field])
            continue
        if (field == "job_posting" and isinstance(before.get("value"), dict) and before["value"].get("status") == "active"
                and before.get("key") in inactive):
            # NAV's feed shows this ad as no longer active; NAV's terms forbid showing inactive ads. A failed or
            # skipped check is not evidence of closure: the job is then kept as a stale last-known value.
            event(before, None, "closed_job")
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
    current["refresh"]["changes_by_type"] = {}
    for change in current["changes"]:
        current["refresh"]["changes_by_type"][change["type"]] = current["refresh"]["changes_by_type"].get(change["type"], 0) + 1
    if not current["legal_identity"] and previous.get("legal_identity"):
        current["legal_identity"] = {**previous["legal_identity"], "stale": True}
    return current
