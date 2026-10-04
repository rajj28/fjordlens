"""Deterministic, evidence-grounded profile synthesis and question answering.

Every sentence carries the claim IDs it rests on. Figures are quoted from filed claims; the only
derived numbers (equity ratio, operating margin) are labelled as calculations and cite both inputs.
Nothing here can introduce a fact that is not already a published claim.
"""
from decimal import Decimal, InvalidOperation
import re

ROLE_LABELS = {"DAGL": "chief executive (daglig leder)", "LEDE": "board chair", "NEST": "deputy chair", "MEDL": "board member",
               "VARA": "deputy board member", "REVI": "auditor", "REGN": "accountant", "KONT": "contact person",
               "INNH": "owner", "DTSO": "partner with joint liability", "DTPR": "partner with proportional liability"}
FAMILY_LABELS = {"identity": "official identity", "financials": "filed annual accounts", "leadership": "registered roles",
                 "locations": "registered workplaces", "group": "group relationships", "filing_history": "filing history",
                 "website": "verified company website", "description": "company-published description",
                 "contact": "company-published contact details", "social_profiles": "company-linked social profiles",
                 "hiring": "hiring signals", "activity": "dated public activity", "workforce": "registered workforce"}


def money(value):
    """Readable amount with the exact filed figure kept alongside."""
    try:
        amount = Decimal(str(value["amount"]))
    except (InvalidOperation, KeyError, TypeError):
        return None
    currency = value.get("currency", "")
    exact = f"{amount:,.0f}".replace(",", " ")
    if abs(amount) >= 1_000_000:
        return f"{currency} {amount / Decimal(1_000_000):.1f} million ({exact})"
    return f"{currency} {exact}"


def summarize(profile):
    """Never raises: a malformed source shape degrades the summary, it never loses the envelope."""
    try:
        return _summarize(profile)
    except Exception as exc:  # noqa: BLE001
        unknowns = [{"family": f, "state": st.get("state"), "reason": st.get("reason")}
                    for f, st in (profile.get("availability") or {}).items() if isinstance(st, dict) and st.get("state") != "available"]
        return {"method": "grounded_templates_v2_fallback", "brief": {"text": "", "claim_ids": []}, "sections": [], "sentences": [],
                "unknowns": unknowns, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _summarize(profile):
    claims = [c for c in profile["claims"] if c.get("availability") == "available"]
    by_field = {}
    for claim in claims:
        by_field.setdefault(claim["field"], []).append(claim)
    sections, flat = [], []

    def section(key, title):
        block = {"key": key, "title": title, "sentences": []}
        sections.append(block)
        return block

    def say(block, text, refs):
        refs = [c for c in refs if c]
        if not text or not refs:
            return
        item = {"text": text, "claim_ids": sorted({c["id"] for c in refs}), "stale": any(c.get("stale") for c in refs)}
        block["sentences"].append(item)
        flat.append(item)

    def first(field):
        values = by_field.get(field) or []
        return values[0] if values else None

    name = profile.get("legal_identity", {}).get("name") or profile["organisation_number"]
    form, industry, address = first("legal_form"), first("industry"), first("registered_address")

    what = section("what_it_does", "What it does")
    if industry:
        nace = industry["value"] if isinstance(industry["value"], dict) else {"kode": str(industry["value"]), "beskrivelse": "description not given"}
        say(what, f"{name} is registered under industry code {nace.get('kode', '?')} ({nace.get('beskrivelse', 'description not given')}).", [industry])
    secondary = [c for c in (first("secondary_industry"),) if c]
    for claim in secondary:
        value = claim["value"] if isinstance(claim["value"], dict) else {"kode": str(claim["value"])}
        say(what, f"Secondary industry: {value.get('kode', '?')} ({value.get('beskrivelse', '')}).", [claim])
    for field, label in (("statutory_purpose", "Statutory purpose (registry)"), ("registered_activity", "Registered activity (registry)")):
        claim = first(field)
        if claim:
            text = " ".join(claim["value"]) if isinstance(claim["value"], list) else str(claim["value"])
            say(what, f"{label}: “{text[:600]}”", [claim])
    description = first("business_description")
    if description:
        say(what, f"The company's verified website describes it as: “{str(description['value'])[:500]}” (self-reported).", [description])
    employees = first("employees")
    if employees:
        say(what, f"The official registry reports {employees['value']} employees.", [employees])
    founded = first("founded_on") or first("registered_on")
    if founded:
        say(what, f"It was {'founded' if founded['field'] == 'founded_on' else 'registered'} on {founded['value']}.", [founded])
    if form:
        say(what, f"Legal form: {form['value']}.", [form])

    lead = section("who_leads_it", "Who leads it")
    roles = [c for c in by_field.get("registered_role", []) if isinstance(c["value"], dict)]
    for code in ("DAGL", "LEDE"):
        holders = [c for c in roles if c["value"].get("role_code") == code]
        if holders:
            say(lead, f"{ROLE_LABELS[code].capitalize()}: " + ", ".join(c["value"]["name"] for c in holders[:3]) + ".", holders[:3])
    board = [c for c in roles if c["value"].get("role_code") in {"LEDE", "NEST", "MEDL"}]
    if board:
        changed = sorted({c.get("effective_at") for c in board if c.get("effective_at")})
        say(lead, f"The registered board has {len(board)} member(s)" + (f"; the board registration was last changed on {changed[-1]}." if changed else "."), board)
    for code in ("REVI", "REGN"):
        holders = [c for c in roles if c["value"].get("role_code") == code]
        if holders:
            say(lead, f"Registered {ROLE_LABELS[code]}: " + ", ".join(str(c["value"]["name"]) for c in holders[:2]) + ".", holders[:2])

    where = section("where_it_operates", "Where it operates")
    if address and isinstance(address["value"], dict):
        a = address["value"]
        street = ", ".join(a.get("adresse") or [])
        say(where, f"Registered business address: {street + ', ' if street else ''}{a.get('postnummer', '')} {a.get('poststed', '')} ({a.get('kommune', 'municipality not given')}).".replace("  ", " "), [address])
    workplaces = [c for c in by_field.get("registered_workplace", []) if isinstance(c["value"], dict)]
    if workplaces:
        places = sorted({(c["value"].get("address") or {}).get("kommune") or (c["value"].get("address") or {}).get("poststed") or "unknown" for c in workplaces})
        staffed = sum(int(c["value"].get("employees") or 0) for c in workplaces)
        say(where, f"{len(workplaces)} registered workplace(s) in {', '.join(places[:6])}{' and more' if len(places) > 6 else ''}"
                   + (f", with {staffed} registered employees across them." if staffed else "."), workplaces)
    site_address = first("website_contact_address")
    if site_address:
        say(where, f"The verified website lists an address: {site_address['value'] if isinstance(site_address['value'], str) else 'see source'} (self-reported).", [site_address])

    numbers = section("latest_numbers", "Latest filed numbers")
    def latest(field, scope="legal_entity"):
        items = [c for c in by_field.get(field, []) if c.get("scope") == scope and c.get("reporting_period")]
        return sorted(items, key=lambda c: c["reporting_period"].get("tilDato", ""))[-1] if items else None
    for scope, label in (("legal_entity", "company accounts"), ("consolidated_group", "group (consolidated) accounts")):
        revenue, operating, net = latest("financial_revenue", scope), latest("financial_operating_profit", scope), latest("financial_net_profit", scope)
        equity, assets, debt = latest("financial_equity", scope), latest("financial_assets", scope), latest("financial_debt", scope)
        anchor = revenue or operating or net or equity or assets
        if not anchor:
            continue
        period = anchor["reporting_period"]
        parts, refs = [], []
        for claim, word in ((revenue, "revenue"), (operating, "operating result"), (net, "annual result")):
            if claim and money(claim["value"]):
                parts.append(f"{word} {money(claim['value'])}")
                refs.append(claim)
        if parts:
            say(numbers, f"For {period.get('fraDato')} to {period.get('tilDato')} ({label}): " + "; ".join(parts) + ".", refs)
        balance = [(equity, "equity"), (assets, "total assets"), (debt, "total debt")]
        bparts = [f"{word} {money(c['value'])}" for c, word in balance if c and money(c["value"])]
        if bparts:
            say(numbers, "Balance sheet at period end: " + "; ".join(bparts) + ".", [c for c, _ in balance if c])
        try:
            if equity and assets and Decimal(assets["value"]["amount"]) > 0 and equity["value"]["currency"] == assets["value"]["currency"]:
                ratio = Decimal(equity["value"]["amount"]) / Decimal(assets["value"]["amount"]) * 100
                say(numbers, f"Calculated equity ratio: {ratio:.1f}% (equity divided by total assets, from the filed figures).", [equity, assets])
            if revenue and operating and Decimal(revenue["value"]["amount"]) > 0 and revenue["value"]["currency"] == operating["value"]["currency"]:
                margin = Decimal(operating["value"]["amount"]) / Decimal(revenue["value"]["amount"]) * 100
                say(numbers, f"Calculated operating margin: {margin:.1f}% (operating result divided by revenue).", [revenue, operating])
        except (InvalidOperation, KeyError, TypeError, ZeroDivisionError):
            pass
    years = by_field.get("available_filing_years") or by_field.get("annual_accounts_filing") or []
    if years:
        values = years[0]["value"] if isinstance(years[0]["value"], list) else [c["value"].get("year") for c in years if isinstance(c["value"], dict)]
        values = sorted({str(v) for v in values if v})
        if values:
            say(numbers, f"Annual accounts are on file for {len(values)} year(s), {values[0]}–{values[-1]}.", years[:1] if isinstance(years[0]["value"], list) else years)

    hiring = section("hiring", "Hiring")
    postings = [c for c in by_field.get("job_posting", []) if isinstance(c["value"], dict)]
    nav = [c for c in postings if c["value"].get("status") == "active"]
    site_posts = [c for c in postings if c not in nav]
    if nav:
        titles = [str(c["value"].get("title")) for c in nav if c["value"].get("title")]
        dated = sorted({c["value"].get("published") for c in nav if c["value"].get("published")})
        agency = any(c["value"].get("posting_context") == "employment_agency" for c in nav)
        say(hiring, f"{len(nav)} job ad(s) marked active in NAV's official job feed name this company as employer"
                    + (" (it is an employment or recruitment agency, so positions may be with its clients)" if agency else "")
                    + (f", published between {dated[0]} and {dated[-1]}" if dated else "")
                    + (": " + "; ".join(titles[:5]) + ("; …" if len(titles) > 5 else "") if titles else "") + ".", nav)
    if site_posts:
        titles = [str(c["value"].get("title")) for c in site_posts if c["value"].get("title")]
        say(hiring, f"{len(site_posts)} job posting(s) are published on the verified website with this company as employer"
                    + (": " + "; ".join(titles[:5]) if titles else "") + " (as published by the company; current availability is not confirmed).", site_posts)

    activity = section("public_activity", "Recent public activity")
    publications = sorted([c for c in by_field.get("company_publication", []) if isinstance(c["value"], dict)],
                          key=lambda c: c["value"].get("published_on") or "", reverse=True)
    for claim in publications[:3]:
        say(activity, f"{claim['value'].get('published_on')}: “{str(claim['value'].get('title'))[:160]}” (company-published).", [claim])
    events = sorted([c for c in by_field.get("registry_event", []) if isinstance(c["value"], dict)], key=lambda c: c.get("effective_at") or "", reverse=True)
    for claim in events[:3]:
        say(activity, f"{claim.get('effective_at')}: {claim['value'].get('description', 'registry update')} (official registry).", [claim])

    online = section("online_presence", "Online presence")
    website = first("official_website")
    if website:
        say(online, f"Verified official website: {website['value']}.", [website])
    profiles = [c for c in by_field.get("company_profile", []) + by_field.get("company_linked_profile", []) if isinstance(c["value"], dict)]
    if profiles:
        say(online, "Company-linked profiles: " + ", ".join(f"{c['value'].get('platform')} ({c['value'].get('url')})" for c in profiles[:6]) + ".", profiles[:6])
    careers = by_field.get("careers_page_url", [])
    if careers:
        say(online, f"The verified website links a careers page: {careers[0]['value'].get('url')} (not by itself evidence of an open position).", careers[:1])
    contacts = by_field.get("website_email", [])[:2] + by_field.get("website_phone", [])[:2]
    if contacts:
        say(online, "Contact details published on the verified website: " + ", ".join(str(c["value"]) for c in contacts) + ".", contacts)
    registered = [c for c in (first("registered_phone"), first("registered_mobile"), first("registered_email")) if c]
    if registered:
        say(online, "Contact details in the official register: " + ", ".join(str(c["value"]) for c in registered) + ".", registered)

    changed = section("what_changed", "What changed")
    changes = profile.get("changes") or []
    material = [c for c in changes if c.get("material")]
    previous = profile.get("refresh", {}).get("previous_run_id")
    by_id = {c["id"]: c for c in profile["claims"]}
    if material:
        for change in material[:6]:
            claim = by_id.get(change["claim_id"])
            label = change.get("field", "fact").replace("_", " ")
            say(changed, f"{change['type'].capitalize()}: {label} (observed {str(change.get('observed_at', ''))[:10]}).", [claim] if claim else [])
    elif previous:
        say(changed, f"No material changes since the previous run ({previous}).", claims[:1])
    stale = [c for c in profile["claims"] if c.get("stale")]
    if stale:
        say(changed, f"{len(stale)} earlier fact(s) could not be re-confirmed in this run and are kept as last-known values, marked stale.", stale[:20])

    unknown_items = []
    for family, state in profile.get("availability", {}).items():
        if state.get("state") != "available":
            verb = "could not be confirmed" if state.get("state") in {"ambiguous", "blocked", "failed"} else "not found"
            if state.get("state") == "not_applicable":
                verb = "not applicable"
            label = FAMILY_LABELS.get(family, family)
            unknown_items.append({"family": family, "state": state.get("state"), "reason": state.get("reason"),
                                  "text": f"{label[0].upper() + label[1:]}: {verb}. {state.get('reason')} ({state.get('state')})."})
    brief_parts = [s["text"] for s in (what["sentences"][:1] + lead["sentences"][:1] + numbers["sentences"][:1] + hiring["sentences"][:1])]
    brief_ids = sorted({i for s in (what["sentences"][:1] + lead["sentences"][:1] + numbers["sentences"][:1] + hiring["sentences"][:1]) for i in s["claim_ids"]})
    return {"method": "grounded_templates_v2", "brief": {"text": " ".join(brief_parts), "claim_ids": brief_ids},
            "sections": [s for s in sections if s["sentences"]], "sentences": flat, "unknowns": unknown_items,
            "change_count": len(changes), "material_change_count": len(material), "stale_claim_count": len(stale),
            "interpretation": "Every sentence cites the claims it rests on. Company-published content is self-reported. "
                              "Calculated ratios are labelled. Missing evidence does not establish absence."}


def answer(profile, question):
    mappings = [(r"financ|revenue|profit|assets|equity|debt|regnskap|omsetning|numbers", {"financials"}),
                (r"lead|ceo|board|director|manage|leder|styre|runs", {"leadership"}),
                (r"job|hir|career|stilling|recruit|working", {"hiring"}),
                (r"news|activ|announc|nyhet", {"activity"}),
                (r"where|location|office|address|workplace|operat", {"locations"}),
                (r"web|social|linkedin|contact|site", {"website", "contact", "social_profiles"}),
                (r"what|does|describe|business|employee|industry", {"identity", "description"})]
    if re.search(r"chang|refresh|update", question, re.I):
        return {"question": question, "answer": f"{len(profile['changes'])} change record(s) against the previous run.", "changes": profile["changes"], "claim_ids": []}
    families = set()
    for pattern, chosen in mappings:
        if re.search(pattern, question, re.I):
            families |= chosen
    if not families:
        return {"question": question, "answer": "This question is not supported by the captured company facts. Ask about financials, leadership, locations, hiring, activity, or changes.", "claim_ids": []}
    claims = [c for c in profile["claims"] if c["family"] in families]
    return {"question": question, "answer": f"Found {len(claims)} supported claim(s) in the selected profile." if claims else "The captured sources do not establish an answer.",
            "claims": claims, "claim_ids": [c["id"] for c in claims],
            "availability": {f: profile["availability"][f] for f in sorted(families)}, "method": "evidence_lookup_v1"}
