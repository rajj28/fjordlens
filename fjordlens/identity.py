"""Conservative exact-entity gates. Name similarity alone never verifies a site."""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlsplit
from .core import org_number
from .html import structured_nodes

DIRECTORIES = {"proff.no", "purehelp.no", "1881.no", "gulesider.no", "brreg.no", "virksomhet.brreg.no", "bizzdo.no"}
NUMBER = r"(?<![0-9])([0-9]{3}[ .\u00a0\u2009\u202f]?[0-9]{3}[ .\u00a0\u2009\u202f]?[0-9]{3})(?![0-9])"
LABEL = r"(?:org(?:anisasjon)?(?:snummer|s?nr|\.?\s*(?:nr|nummer))\.?|foretaks(?:nummer|nr\.?)|organisation\s*(?:number|no\.?)|organization\s*(?:number|no\.?)|MVA(?:[ -]?nr\.?)?|VAT(?:\s*(?:no\.?|number))?)"
ORGANIZATION_TYPES = {"Organization", "Corporation", "LocalBusiness", "ProfessionalService", "Store", "Restaurant", "Dentist", "AutoDealer", "AutoRepair", "HomeAndConstructionBusiness", "NGO", "EducationalOrganization", "GovernmentOrganization"}

def normalize_name(value):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", str(value or "")).casefold()).strip()

def contains_name(text, name):
    return bool(name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text))

def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")

def legal_numbers(text):
    found = {}
    patterns = [LABEL + r"\s*[:#-]?\s*(?:NO\s*)?" + NUMBER, r"\bNO\s*" + NUMBER + r"\s*MVA\b"]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            try:
                number = org_number(match.group(1))
                found[number] = {"span": match.group(0), "start": match.start(), "end": match.end()}
            except ValueError:
                pass
    return found

def jsonld_types(node):
    """Ignore malformed type values without aborting the surrounding page."""
    value = node.get("@type") if isinstance(node, dict) else None
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return {value for value in values if isinstance(value, str)}

def node_org_identifiers(node):
    """Keep each legally labeled identifier and its exact relative JSON pointer."""
    if not isinstance(node, dict):
        return []
    identifiers = []
    for field in ("taxID", "vatID", "identifier"):
        values = node.get(field)
        is_list = isinstance(values, list)
        values = values if is_list else [values]
        for index, value in enumerate(values):
            pointer = "/" + field + (f"/{index}" if is_list else "")
            if isinstance(value, dict):
                if field == "identifier" and not re.search(r"org|vat|mva|foretak|business.?registration", str(value.get("propertyID", "")), re.I):
                    continue
                value = value.get("value")
                pointer += "/value"
            elif field == "identifier":
                # A generic numerical identifier can be a DUNS/SKU, not a Norwegian org number.
                if not re.fullmatch(r"NO\s*" + NUMBER + r"\s*MVA", str(value), re.I):
                    continue
            if value is None or value == "":
                continue
            number = None
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                match = re.fullmatch(r"(?:NO\s*)?" + NUMBER + r"(?:\s*MVA)?", str(value).strip(), re.I)
                if match:
                    try:
                        number = org_number(match.group(1))
                    except ValueError:
                        pass
            identifiers.append({"organisation_number": number, "value": value, "pointer": pointer})
    return identifiers

def node_orgs(node):
    return {item["organisation_number"] for item in node_org_identifiers(node) if item["organisation_number"]}

def node_org(node):
    identifiers = node_org_identifiers(node)
    numbers = {item["organisation_number"] for item in identifiers}
    return next(iter(numbers)) if len(numbers) == 1 and None not in numbers else None

def structured_org_proofs(page):
    """Collect all organization IDs; a mention is not an ownership decision."""
    proofs = {}
    for node, path in structured_nodes(page["jsonld"]):
        if not jsonld_types(node) & ORGANIZATION_TYPES:
            continue
        for item in node_org_identifiers(node):
            number = item["organisation_number"]
            if number:
                proofs.setdefault(number, []).append({
                    "node": node, "value": item["value"],
                    "selector": {"type": "parsed_html_pointer", "value": "/jsonld" + path + item["pointer"]}})
    return proofs

def page_org_numbers(page):
    return set(legal_numbers(page["text"])) | set(structured_org_proofs(page))

def assess(entity, page, *, registry_candidate=False):
    org, name = entity["organisasjonsnummer"], entity["navn"]
    h = host(page["url"])
    base = {"organisation_number": org, "url": page["url"], "method": "exact_legal_context_v1", "publishable": False,
            "state": "ambiguous", "proof_span": "", "scope": "unresolved"}
    if any(h == d or h.endswith("." + d) for d in DIRECTORIES):
        return {**base, "reason": "Directory pages cannot establish an official website"}
    text = page["text"]
    if not normalize_name(name):
        return {**base, "reason": "Missing legal name"}
    if any(marker in text.lower() for marker in ("domain is for sale", "domain for sale", "buy this domain", "hugedomains", "domenet er til salgs", "dette domenet er parkert")):
        return {**base, "reason": "Parked or for-sale domain"}
    numbers = legal_numbers(text)
    structured_proofs = structured_org_proofs(page)
    identifiers = set(numbers) | set(structured_proofs)
    if identifiers - {org}:
        return {**base, "reason": "Conflicting or multiple legal entities; global site facts quarantined", "observed_organisation_numbers": sorted(identifiers), "scope": "multi_entity"}
    if org not in identifiers:
        # A registered URL plus exact legal name, full street/postcode AND a
        # matching registry telephone is a separate, conjunctive proof route.
        # No fuzzy names, approximate addresses or phone-digit concatenation.
        normalized = normalize_name(name)
        title = normalize_name(page["title"] + " " + page["meta"].get("og:site_name", ""))
        core = re.sub(r"\s+(as|asa|ans|da|sa|nuf)$", "", normalized)
        address = entity.get("forretningsadresse") or {}
        streets = address.get("adresse") or []
        postcode = str(address.get("postnummer") or "")
        phone = re.sub(r"\D", "", str(entity.get("telefon") or ""))
        phone = phone[2:] if phone.startswith("47") and len(phone) == 10 else phone
        page_text = normalize_name(text)
        street = next((s for s in streets if len(normalize_name(s)) >= 6 and normalize_name(s) in page_text), None)
        phone_match = False
        if re.fullmatch(r"\d{8}", phone):
            for contact in page.get("contacts", []):
                if contact.lower().startswith("tel:"):
                    digits = re.sub(r"\D", "", contact[4:])
                    if digits in {phone, "47" + phone}:
                        phone_match = True
            pattern = r"(?:telefon|phone|tlf\.?|tel\.?)\s*:?\s*(?:\+?47\s*)?" + r"[ .()\-]*".join(phone) + r"(?!\d)"
            phone_match = phone_match or bool(re.search(pattern, text, re.I))
        if (registry_candidate and len(core) >= 4 and contains_name(title, core) and contains_name(page_text, normalized)
                          and street and re.fullmatch(r"\d{4}", postcode) and re.search(r"(?<!\d)" + postcode + r"(?!\d)", text) and phone_match):
            return {**base, "publishable": True, "state": "available", "scope": "exact_legal_entity",
                    "method": "registry_name_address_phone_v1", "reason": "Registry-listed site, exact legal name, street, postcode and telephone all agree",
                    "registry_candidate": True, "proof_span": name,
                    "corroboration": {"legal_name": name, "street": street, "postcode": postcode, "telephone": phone}}
        return {**base, "reason": "No exact organisation number in legal context; name or registry URL alone is insufficient"}
    normalized = normalize_name(name)
    core = re.sub(r"\s+(as|asa|ans|da|sa|nuf)$", "", normalized)
    title = normalize_name(page["title"] + " " + page["meta"].get("og:site_name", ""))
    name_visible = contains_name(normalize_name(text), normalized) or bool(core and len(core) >= 4 and contains_name(title, core))
    candidates = structured_proofs.get(org, [])
    structured_proof = next((proof for proof in candidates if normalize_name(proof["node"].get("legalName") or proof["node"].get("name")) == normalized), candidates[0] if candidates else None)
    snode = structured_proof["node"] if structured_proof else None
    structured_name_match = snode and normalize_name(snode.get("legalName") or snode.get("name")) == normalized
    if not name_visible and not structured_name_match:
        return {**base, "reason": "Organisation number found without corroborating company name"}
    if not registry_candidate:
        email = str(entity.get("epostadresse") or "").lower()
        registry_domain_match = email.endswith("@" + h)
        structured_owner = (structured_name_match and isinstance(snode.get("url"), str) and host(snode["url"]) == h
                            and normalize_name(page["meta"].get("og:site_name", "")) in {normalized, core} and len(core) >= 4)
        if not registry_domain_match and not structured_owner:
            return {**base, "reason": "Discovered page proves company mention but lacks first-party ownership corroboration"}
    proof = numbers.get(org, {}).get("span")
    proof_selector = None
    if not proof:
        proof = str(structured_proof["value"])
        proof_selector = structured_proof["selector"]
    return {**base, "publishable": True, "state": "available", "scope": "exact_legal_entity",
            "reason": "Exact organisation number in legal context with corroborating company name", "proof_span": proof,
            "proof_selector": proof_selector,
            "registry_candidate": registry_candidate, "observed_organisation_numbers": [org]}
