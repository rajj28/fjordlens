"""Exact-org official connectors. Financial values never come from web prose."""
from __future__ import annotations
import json
import re
from decimal import Decimal, InvalidOperation
from datetime import date
from .core import at, canonical

BASE = "https://data.brreg.no/enhetsregisteret/api"
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap"
ENTITY_FIELDS = {
    "legal_name": "/navn", "legal_form": "/organisasjonsform/kode", "registered_address": "/forretningsadresse",
    "postal_address": "/postadresse", "industry": "/naeringskode1", "secondary_industry": "/naeringskode2",
    "founded_on": "/stiftelsesdato", "registered_on": "/registreringsdatoEnhetsregisteret",
    "bankrupt": "/konkurs", "liquidating": "/underAvvikling", "vat_registered": "/registrertIMvaregisteret",
    "latest_filing_year": "/sisteInnsendteAarsregnskap", "registered_activity": "/aktivitet",
    "statutory_purpose": "/vedtektsfestetFormaal", "registry_website_candidate": "/hjemmeside",
    "registered_phone": "/telefon", "registered_email": "/epostadresse", "registered_mobile": "/mobil",
    "tertiary_industry": "/naeringskode3", "institutional_sector": "/institusjonellSektorkode",
    "registered_in_business_register": "/registrertIForetaksregisteret", "business_register_date": "/registreringsdatoForetaksregisteret",
    "registered_in_foundation_register": "/registrertIStiftelsesregisteret", "registered_in_voluntary_register": "/registrertIFrivillighetsregisteret",
    "vat_registration_date": "/registreringsdatoMerverdiavgiftsregisteret", "employees_registered_on": "/registreringsdatoAntallAnsatteEnhetsregisteret",
    "written_language": "/maalform",
}
# Registry contact details are official contact facts, and the registry's own statements of what the
# company does are official descriptions; neither is an identity fact.
FIELD_FAMILY = {"registered_phone": "contact", "registered_email": "contact", "registered_mobile": "contact",
                "registered_activity": "description", "statutory_purpose": "description",
                "employees_registered_on": "workforce"}
MONEY_FIELDS = {
    "revenue": "/resultatregnskapResultat/driftsresultat/driftsinntekter/sumDriftsinntekter",
    "operating_profit": "/resultatregnskapResultat/driftsresultat/driftsresultat",
    "profit_before_tax": "/resultatregnskapResultat/ordinaertResultatFoerSkattekostnad",
    "net_profit": "/resultatregnskapResultat/aarsresultat", "assets": "/eiendeler/sumEiendeler",
    "equity": "/egenkapitalGjeld/egenkapital/sumEgenkapital", "debt": "/egenkapitalGjeld/gjeldOversikt/sumGjeld",
}

def fetch_json(profile, fetcher, url, family):
    r = fetcher.get(url, profile.data["organisation_number"], official=True)
    profile.snapshot(r)
    if r.state != "available":
        profile.error(family, r)
        return r, None
    data = r.json()
    if data is None:
        profile.state(family, "failed", "Official endpoint returned malformed JSON")
    return r, data

def identity(profile, fetcher):
    org = profile.data["organisation_number"]
    r, data = fetch_json(profile, fetcher, BASE + "/enheter/" + org, "identity")
    if not isinstance(data, dict):
        return None
    if data.get("organisasjonsnummer") != org:
        profile.state("identity", "ambiguous", "Official response organisation number differs from input")
        return None
    if not isinstance(data.get("navn"), str) or not data["navn"].strip():
        profile.state("identity", "failed", "Official identity response lacks a legal name")
        return None
    profile.identity_response = r  # Evidence for registry-declared links published later.
    profile.data["legal_identity"] = {"organisation_number": org, "name": data.get("navn"),
                                     "legal_form": at(data, "/organisasjonsform/kode"), "source_snapshot_id": r.snapshot_id}
    for field, pointer in ENTITY_FIELDS.items():
        value = at(data, pointer)
        if value not in (None, "", []):
            profile.add(field, value, r, pointer=pointer, family=FIELD_FAMILY.get(field, "identity"))
    # Registered workforce (Aa-registeret count as held by the entity register): its own family.
    if data.get("harRegistrertAntallAnsatte") is True and data.get("antallAnsatte") is not None:
        profile.add("employees", data["antallAnsatte"], r, pointer="/antallAnsatte", family="workforce",
                    effective_at=data.get("registreringsdatoAntallAnsatteNAVAaregisteret"))
    elif data.get("harRegistrertAntallAnsatte") is False:
        profile.state("workforce", "not_available", "The registry holds no registered employees for this entity (harRegistrertAntallAnsatte=false); not shown as zero")
    return data

def leadership(profile, fetcher):
    org = profile.data["organisation_number"]
    r, data = fetch_json(profile, fetcher, BASE + "/enheter/" + org + "/roller", "leadership")
    if not isinstance(data, dict):
        return
    if not isinstance(data.get("rollegrupper"), list):
        profile.state("leadership", "failed", "Roles response lacks the expected role-group array")
        return
    if at(data, "/_links/enhet/href") not in {None, BASE + "/enheter/" + org}:
        profile.state("leadership", "ambiguous", "Roles endpoint identifies a different legal entity")
        return
    # Validate the entire set before publishing: an unparseable active role
    # cannot establish that a previously observed role has been removed.
    prepared = []
    try:
        for gi, group in enumerate(data["rollegrupper"]):
            if not isinstance(group, dict) or not isinstance(group.get("roller"), list):
                raise ValueError("Role group lacks the expected roles array")
            for ri, role in enumerate(group["roller"]):
                if not isinstance(role, dict) or not isinstance(role.get("avregistrert", False), bool):
                    raise ValueError("Malformed role or registration status")
                person = role.get("person") or {}
                if not isinstance(person, dict) or not isinstance(person.get("erDoed", False), bool):
                    raise ValueError("Malformed role person or deceased status")
                if role.get("avregistrert") or person.get("erDoed"):
                    continue
                parts, entity = person.get("navn") or {}, role.get("enhet") or {}
                if not isinstance(parts, dict) or not isinstance(entity, dict):
                    raise ValueError("Malformed active role identity")
                names = [parts.get(k) for k in ("fornavn", "mellomnavn", "etternavn") if parts.get(k)]
                if any(not isinstance(part, str) for part in names):
                    raise ValueError("Malformed active role name")
                name = " ".join(names) or entity.get("navn") or ""
                if isinstance(name, list) and all(isinstance(part, str) for part in name):
                    name = " ".join(name)
                role_code = at(role, "/type/kode")
                if not isinstance(name, str) or not name.strip() or not isinstance(role_code, str) or not role_code.strip():
                    raise ValueError("Active role lacks a name or role code")
                value = {"name": name, "role": at(role, "/type/beskrivelse"), "role_code": role_code,
                         "organisation_number": entity.get("organisasjonsnummer")}
                prepared.append((f"/rollegrupper/{gi}/roller/{ri}", value, group.get("sistEndret")))
    except ValueError as exc:
        profile.state("leadership", "failed", str(exc), complete=False)
        return
    for pointer, value, effective_at in prepared:
        profile.add("registered_role", value, r, pointer=pointer,
                    span=canonical(value), family="leadership", key=f"{value['role_code']}:{value['name']}:{value['organisation_number']}",
                    effective_at=effective_at, source_class="official_roles")
    count = len(prepared)
    profile.state("leadership", "available" if count else "not_available", f"{count} active registered roles", complete=True)

def financials(profile, fetcher):
    org = profile.data["organisation_number"]
    r, data = fetch_json(profile, fetcher, ACCOUNTS + "/" + org, "financials")
    if data is None:
        return
    # Decimal lexical values are kept exactly, never converted through binary float.
    data = json.loads(r.body, parse_float=str)
    if not isinstance(data, list):
        profile.state("financials", "failed", "Unexpected financial schema")
        return
    seen, count, missing = set(), 0, []
    records = sorted(enumerate(data), key=lambda pair: str(pair[1].get("journalnr", "")), reverse=True)
    for index, item in records:
        if at(item, "/virksomhet/organisasjonsnummer") != org:
            profile.data["errors"].append({"family": "financials", "type": "wrong_entity_record_rejected"})
            continue
        period, currency, account_type = item.get("regnskapsperiode"), item.get("valuta"), item.get("regnskapstype")
        if not isinstance(period, dict) or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(period.get(k, ""))) for k in ("fraDato", "tilDato")) or not re.fullmatch("[A-Z]{3}", str(currency)):
            continue
        try:
            if date.fromisoformat(period["fraDato"]) > date.fromisoformat(period["tilDato"]):
                continue
        except ValueError:
            continue
        if account_type not in {"SELSKAP", "KONSERN"}:
            continue
        record_key = f"{period['fraDato']}:{period['tilDato']}:{account_type}"
        if record_key in seen:
            continue
        seen.add(record_key)
        for name, path in MONEY_FIELDS.items():
            amount = at(item, path)
            if amount is None:
                missing.append({"metric": name, "period": period, "account_type": account_type, "state": "not_available"})
                continue
            try:
                if isinstance(amount, bool) or not Decimal(str(amount)).is_finite():
                    continue
            except InvalidOperation:
                continue
            value = {"amount": str(amount), "currency": currency, "unit": "currency_units",
                     "account_type": account_type, "record_id": item.get("id")}
            profile.add("financial_" + name, value, r, pointer=f"/{index}" + path,
                        family="financials", key=record_key, reporting_period=period,
                        source_class="official_annual_accounts", scope="legal_entity" if account_type == "SELSKAP" else "consolidated_group")
            count += 1
    profile.state("financials", "available" if count else "not_available", f"{count} exact-company filed amounts; original currency and period retained", missing_fields=missing)

def locations(profile, fetcher):
    org = profile.data["organisation_number"]
    r, data = fetch_json(profile, fetcher, BASE + f"/underenheter?overordnetEnhet={org}&size=1000", "locations")
    if not isinstance(data, dict):
        return
    rows = at(data, "/_embedded/underenheter")
    if rows is None and at(data, "/page/totalElements") == 0:
        rows = []
    if not isinstance(rows, list):
        profile.state("locations", "failed", "Workplace response lacks rows or an explicit empty page")
        return
    count = 0
    for i, item in enumerate(rows):
        if item.get("overordnetEnhet") != org:
            continue
        value = {"organisation_number": item.get("organisasjonsnummer"), "name": item.get("navn"),
                 "address": item.get("beliggenhetsadresse"), "industry": item.get("naeringskode1"),
                 "employees": item.get("antallAnsatte") if item.get("harRegistrertAntallAnsatte") else None}
        # Contact details the company registered for this workplace (only when present).
        for source_key, target in (("telefon", "phone"), ("mobil", "mobile"), ("epostadresse", "email"), ("hjemmeside", "website")):
            if isinstance(item.get(source_key), str) and item[source_key].strip():
                value[target] = item[source_key]
        profile.add("registered_workplace", value, r, pointer=f"/_embedded/underenheter/{i}", family="locations",
                    key=value["organisation_number"], source_class="official_subunits", scope="registered_subunit")
        if item.get("harRegistrertAntallAnsatte") is True and item.get("antallAnsatte") is not None:
            profile.add("workplace_employees", item["antallAnsatte"], r, pointer=f"/_embedded/underenheter/{i}/antallAnsatte", family="workforce",
                        key=value["organisation_number"], source_class="official_subunits", scope="registered_subunit",
                        effective_at=item.get("registreringsdatoAntallAnsatteNAVAaregisteret"))
        for source_key, field in (("telefon", "workplace_phone"), ("mobil", "workplace_mobile"), ("epostadresse", "workplace_email")):
            if isinstance(item.get(source_key), str) and item[source_key].strip():
                profile.add(field, item[source_key], r, pointer=f"/_embedded/underenheter/{i}/{source_key}", family="contact",
                            key=f"{value['organisation_number']}:{item[source_key]}", source_class="official_subunits", scope="registered_subunit")
        count += 1
    pages = at(data, "/page/totalPages")
    total = at(data, "/page/totalElements")
    complete = isinstance(pages, int) and pages <= 1 and total == count
    profile.state("locations", "available" if count else "not_available", f"{count} exact-parent registered workplaces", complete=complete)

def group(profile, fetcher, entity):
    if entity.get("erIKonsern") is False:
        profile.state("group", "not_applicable", "Registry explicitly reports no group membership")
        return
    org = profile.data["organisation_number"]
    r, data = fetch_json(profile, fetcher, BASE + "/konsernstruktur/" + org, "group")
    if not isinstance(data, dict):
        return
    count = 0
    def walk(item, path="", depth=0):
        nonlocal count
        if depth > 15 or count > 1000:
            return
        parent, child = item.get("parentOrganisasjonsnummer"), item.get("organisasjonsnummer")
        if parent and child and org in {parent, child}:
            value = {"parent_organisation_number": parent, "child_organisation_number": child,
                     "name": item.get("navn"), "relationship": at(item, "/knytningsform/beskrivelse"), "basis": item.get("grunnlag")}
            profile.add("group_relationship", value, r, pointer=path, span=canonical(value), family="group",
                        key=f"{parent}:{child}", effective_at=item.get("dato"), source_class="official_group_structure", scope="relationship")
            count += 1
        for i, child_item in enumerate(item.get("children", [])):
            walk(child_item, path + f"/children/{i}", depth + 1)
    walk(data)
    if not count:
        profile.state("group", "not_available", "No direct group relationships returned")

def history(profile, fetcher):
    org = profile.data["organisation_number"]
    r, data = fetch_json(profile, fetcher, ACCOUNTS + f"/aarsregnskap/kopi/{org}/aar", "filing_history")
    if not isinstance(data, list):
        return
    years = sorted({str(v) for v in data if re.fullmatch(r"\d{4}", str(v))}, reverse=True)
    if years:
        profile.add("available_filing_years", years, r, family="filing_history", source_class="official_annual_accounts")
        profile.data["filing_documents"] = [{"year": y, "url": ACCOUNTS + f"/aarsregnskap/kopi/{org}/{y}",
                                               "state": "not_available", "note": "Official download link; document not extracted"} for y in years]
    else:
        profile.state("filing_history", "not_available", "No filing years returned")
