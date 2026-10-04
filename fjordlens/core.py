from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import uuid
from . import jsonspan

STATES = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
FAMILIES = ("identity", "financials", "leadership", "locations", "group", "filing_history",
            "website", "description", "contact", "social_profiles", "hiring", "activity", "workforce")
OFFICIAL_CLASSES = {"official_registry", "official_roles", "official_subunits", "official_annual_accounts", "official_group_structure"}
SOURCE_CLASSES = OFFICIAL_CLASSES | {"company_owned", "public_job_feed"}

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()

def org_number(value: Any) -> str:
    number = re.sub(r"[\s.\-]", "", str(value or ""))
    if not re.fullmatch(r"[0-9]{9}", number):
        raise ValueError("Organisation number must contain nine digits")
    check = 11 - sum(int(n) * w for n, w in zip(number[:8], (3, 2, 7, 6, 5, 4, 3, 2))) % 11
    check = 0 if check == 11 else check
    if check == 10 or check != int(number[-1]):
        raise ValueError("Organisation number checksum is invalid")
    return number

def at(value: Any, pointer: str, default=None):
    try:
        for part in pointer.strip("/").split("/") if pointer else []:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        return default

def write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def raw_json_quote(response, pointer, max_len=4000):
    """Exact source bytes for a JSON-pointer claim, e.g. '"navn":"DIPS AS"' (cached per response)."""
    cached = getattr(response, "_json_spans", None)
    if cached is None:
        text = response.text()
        try:
            cached = (text, jsonspan.index(text))
        except ValueError:
            cached = (text, {})
        try:
            response._json_spans = cached
        except AttributeError:
            pass
    text, spans = cached
    span = spans.get(pointer)
    if not span:
        return None
    member, start, end = span
    if end - member <= max_len:
        return text[member:end]
    if end - start <= max_len:
        return text[start:end]
    return text[start:start + max_len]  # Verbatim prefix of an over-long value; never re-serialised.

class Profile:
    def __init__(self, org: str, run_id: str):
        self.lock = threading.RLock()  # Stages of one company may run on different threads.
        self.data = {
            "schema_version": "1.0.0", "organisation_number": org,
            "run": {"run_id": run_id, "started_at": now(), "terminal_status": "completed"},
            "legal_identity": {}, "claims": [], "evidence": [], "source_snapshots": [],
            "availability": {f: {"state": "not_available", "reason": "Not yet researched"} for f in FAMILIES},
            "identity_assessments": [], "attempts": [], "changes": [], "errors": [],
            "refresh": {"previous_run_id": None, "preserved_evidence": [], "stale_claim_ids": []},
            "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0},
        }

    def state(self, family: str, state: str, reason: str, **extra):
        with self.lock:
            return self._state(family, state, reason, **extra)

    def _state(self, family: str, state: str, reason: str, **extra):
        if state not in STATES or family not in FAMILIES:
            raise ValueError("Invalid availability state or family")
        self.data["availability"][family] = {"state": state, "reason": reason, **extra}

    def snapshot(self, response):
        with self.lock:
            return self._snapshot(response)

    def _snapshot(self, response):
        record = response.metadata()
        if record["id"] not in {s["id"] for s in self.data["source_snapshots"]}:
            self.data["source_snapshots"].append(record)

    def add(self, *args, **kwargs):
        # The verbatim quote can be expensive on large responses: compute it before taking the
        # profile lock so finalization at the deadline never waits on JSON indexing.
        if kwargs.get("method", "json_pointer_v1") == "json_pointer_v1" and len(args) >= 3:
            kwargs["_verbatim"] = raw_json_quote(args[2], kwargs.get("pointer", ""))
        with self.lock:
            return self._add(*args, **kwargs)

    def _add(self, field: str, value: Any, response, *, pointer: str = "", span: str = "",
            family: str, key: str = "", effective_at=None, reporting_period=None,
            method="json_pointer_v1", source_class="official_registry", scope="legal_entity", identity_proof=None, selector=None,
            _verbatim=None):
        if value is None or value == "" or value == [] or value == {} or (isinstance(value, str) and not value.strip()):
            return None
        canonical(value)  # Reject non-finite/non-serializable values before publication.
        if family not in FAMILIES or source_class not in SOURCE_CLASSES:
            raise ValueError("Unknown claim family or source class")
        source = urlsplit(response.url)
        if source.scheme not in {"http", "https"} or not source.hostname or source.username or source.password:
            raise ValueError("Unsafe evidence source URL")
        canonical_org = org_number(self.data["organisation_number"])
        if canonical_org != self.data["organisation_number"]:
            raise ValueError("Claim identity must use canonical organisation number")
        if source_class in OFFICIAL_CLASSES and (source.hostname != "data.brreg.no" or canonical_org not in response.url):
            raise ValueError("Official provenance requires the exact-company official endpoint")
        if source_class == "company_owned" and (not identity_proof or not identity_proof.get("publishable") or identity_proof.get("organisation_number") != canonical_org):
            raise ValueError("Company-owned claims require an accepted exact-entity proof")
        verbatim = None
        if method == "json_pointer_v1":
            missing = object()
            extracted = at(response.json(), pointer, missing)
            if extracted is missing or extracted is None:
                raise ValueError("Claim JSON pointer is absent or null")
            if not isinstance(value, (dict, list)) and not isinstance(extracted, (dict, list)) and extracted != value:
                raise ValueError("Claim value differs from the source value at its JSON pointer")
            verbatim = _verbatim if _verbatim is not None else raw_json_quote(response, pointer)
        if family == "financials":
            if source_class != "official_annual_accounts" or not isinstance(value, dict) or not reporting_period:
                raise ValueError("Financial publication requires official accounts and period")
            if not re.fullmatch(r"[A-Z]{3}", str(value.get("currency", ""))) or value.get("unit") != "currency_units":
                raise ValueError("Financial publication requires explicit currency and units")
            amount = Decimal(str(value.get("amount")))
            if not amount.is_finite():
                raise ValueError("Non-finite financial amount")
            raw = json.loads(response.body, parse_float=str)
            raw_value = at(raw, pointer)
            index = int(pointer.split("/")[1])
            record = raw[index]
            expected_scope = {"SELSKAP": "legal_entity", "KONSERN": "consolidated_group"}.get(record.get("regnskapstype"))
            if (Decimal(str(raw_value)) != amount or record.get("valuta") != value["currency"]
                or record.get("regnskapsperiode") != reporting_period or scope != expected_scope
                or at(record, "/virksomhet/organisasjonsnummer") != canonical_org):
                raise ValueError("Financial claim does not match source amount, identity, currency, period or scope")
        self.snapshot(response)
        evid = {"snapshot_id": response.snapshot_id, "source_url": response.url,
                "source_class": source_class, "retrieved_at": response.retrieved_at,
                "content_sha256": response.sha256, "extraction_method": method,
                "selector": selector or ({"type": "json_pointer", "value": pointer} if method == "json_pointer_v1" else {"type": "normalized_text", "value": span}),
                "claim_span": verbatim or span or canonical(at(response.json(), pointer))[:4000],
                "quote_kind": "verbatim_json_member" if verbatim else ("verbatim_text" if span and span in response.text() else "normalized_text"),
                "effective_at": effective_at, "reporting_period": reporting_period,
                "identity_proof": identity_proof or {"method": "exact_org_endpoint", "organisation_number": self.data["organisation_number"]}}
        evid["id"] = "ev_" + digest(evid)[:24]
        if evid["id"] not in {e["id"] for e in self.data["evidence"]}:
            self.data["evidence"].append(evid)
        stable = ["claim-v1", self.data["organisation_number"], family, field, key, scope, reporting_period]
        claim = {"id": "cl_" + digest(stable)[:24], "field": field, "family": family, "key": key,
                 "value": value, "availability": "available", "scope": scope,
                 "confidence": 1.0 if source_class.startswith("official") else 0.97,
                 "evidence_ids": [evid["id"]], "effective_at": effective_at, "reporting_period": reporting_period,
                 "first_observed_at": response.retrieved_at, "last_observed_at": response.retrieved_at, "stale": False}
        previous = next((c for c in self.data["claims"] if c["id"] == claim["id"]), None)
        if previous:
            if previous["value"] == value:
                previous["evidence_ids"] = sorted(set(previous["evidence_ids"] + [evid["id"]]))
                return previous
            self.data["errors"].append({"family": family, "type": "conflicting_claim", "field": field, "key": key,
                                        "retained_value": previous["value"], "candidate_value": value, "candidate_evidence_id": evid["id"]})
            return None
        self.data["claims"].append(claim)
        self.state(family, "available", "Published claims have source-level evidence")
        return claim

    def error(self, family: str, response):
        with self.lock:
            return self._error(family, response)

    def _error(self, family: str, response):
        state = response.state
        self.state(family, state, response.error or f"HTTP {response.status}")
        self.snapshot(response)
        self.data["errors"].append({"family": family, "type": state, "source_url": response.url,
                                    "message": response.error or f"HTTP {response.status}"})

def validate(profile: dict) -> list[str]:
    errors = []
    if not profile.get("organisation_number"):
        errors.append("missing organisation number")
    if set(FAMILIES) - set(profile.get("availability", {})):
        errors.append("missing availability sections")
    if any(v.get("state") not in STATES for v in profile.get("availability", {}).values()):
        errors.append("invalid availability state")
    evidence = {e["id"]: e for e in profile.get("evidence", [])}
    snapshots = {s["id"]: s for s in profile.get("source_snapshots", [])}
    seen = set()
    for c in profile.get("claims", []):
        if c["id"] in seen:
            errors.append("duplicate claim id")
        seen.add(c["id"])
        if c.get("value") is None or not c.get("evidence_ids"):
            errors.append("unsupported available claim")
        if c.get("family") not in FAMILIES:
            errors.append("unknown claim family")
        for ref in c.get("evidence_ids", []):
            e = evidence.get(ref, {})
            if not e or not e.get("retrieved_at") or not e.get("source_url") or len(e.get("content_sha256", "")) != 64:
                errors.append("invalid evidence reference")
            if e.get("snapshot_id") not in snapshots:
                errors.append("missing snapshot")
            parsed = urlsplit(e.get("source_url", ""))
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                errors.append("unsafe evidence source URL")
            if e.get("source_class") not in SOURCE_CLASSES:
                errors.append("unknown evidence source class")
            if e.get("source_class") in OFFICIAL_CLASSES and parsed.hostname != "data.brreg.no":
                errors.append("official provenance host mismatch")
            if not e.get("claim_span") or e.get("claim_span") == "null":
                errors.append("empty evidence span")
            snap = snapshots.get(e.get("snapshot_id"), {})
            if snap and (snap.get("content_sha256") != e.get("content_sha256") or snap.get("final_url") != e.get("source_url")):
                errors.append("snapshot provenance mismatch")
        if c["family"] == "financials" and not c.get("reporting_period"):
            errors.append("financial claim missing period")
    return sorted(set(errors))
