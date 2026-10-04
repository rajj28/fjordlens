"""Standard questions answered only from an envelope's claims (machine-readable `answers` block).

Every answer lists exactly the claims its text uses and their evidence ids. An unanswerable question says what is
unknown and why (from the family's availability reason) and never turns absence into zero or "no".
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

QUESTIONS = (
    "What does the company do?", "Who leads the company?", "Where does it operate?", "What are its latest filed numbers?",
    "Is it hiring?", "Does it have a verified website?", "How many people does it employ?",
    "What changed since the last check?", "What dated public activity is there?", "How can it be contacted?",
    "Is it in financial distress?",
)
CHANGE_WORDS = {
    "changed_name": "registered name changed", "changed_legal_form": "legal form changed", "changed_address": "registered address changed",
    "changed_status": "registry status changed", "changed_registry_website": "website in the register changed",
    "changed_employee_count": "registered employee count changed", "changed_industry": "industry code changed",
    "new_filing": "new annual accounts on file", "changed_financials": "filed figures changed", "new_role": "new registered role",
    "removed_role": "registered role removed", "changed_role": "registered role changed", "new_location": "new registered workplace",
    "removed_location": "registered workplace removed", "new_job": "new job ad", "closed_job": "job ad no longer active",
    "new_publication": "new company publication", "changed_website": "verified website changed",
}


def nok(amount: Any) -> str:
    """Whole currency units with a space thousands separator, computed exactly."""
    try:
        value = Decimal(str(amount)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return str(amount)
    sign = "-" if value < 0 else ""
    return sign + f"{abs(int(value)):,}".replace(",", " ")


def period_key(claim):
    period = claim.get("reporting_period") or {}
    return (str(period.get("fraDato") or ""), str(period.get("tilDato") or ""))


def latest_period(financial):
    """The latest complete reporting period: latest end date; on a tie the period with most figures, then the longest."""
    counts = {}
    for claim in financial:
        counts[period_key(claim)] = counts.get(period_key(claim), 0) + 1
    return max(counts, key=lambda key: (key[1], counts[key], [-ord(ch) for ch in key[0]]))


def build_answers(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    availability = envelope.get("availability", {}) or {}
    claims = [c for c in envelope.get("claims", []) if c.get("availability") == "available" and not c.get("stale")]
    by_field: dict[str, list[dict]] = {}
    for claim in claims:
        by_field.setdefault(claim.get("field"), []).append(claim)
    first = lambda field: (by_field.get(field) or [None])[0]  # noqa: E731
    current_ids = {c.get("id") for c in claims}  # a removed fact's claim no longer exists; its change cites evidence
    answers = []

    def answered(question, text, used):
        used = [c for c in used if c]
        answers.append({"question": question, "answerable": True, "answer": text, "claim_ids": [c["id"] for c in used],
                        "evidence_ids": sorted({e for c in used for e in c.get("evidence_ids", [])})})

    def unknown(question, text):
        answers.append({"question": question, "answerable": False, "answer": text, "claim_ids": [], "evidence_ids": []})

    def reason(family, default):
        return str((availability.get(family) or {}).get("reason") or default)

    # 1. What it does.
    industry, activity, purpose, description = first("industry"), first("registered_activity"), first("statutory_purpose"), first("business_description")
    used, parts = [], []
    if industry and isinstance(industry["value"], dict):
        parts.append(f"Industry {industry['value'].get('kode', '?')}: {industry['value'].get('beskrivelse', '')}.")
        used.append(industry)
    text_claim = activity or purpose
    if text_claim:
        value = text_claim["value"]
        text = " ".join(value) if isinstance(value, list) else str(value)
        parts.append(("Registered activity: " if text_claim is activity else "Statutory purpose: ") + text[:300])
        used.append(text_claim)
    elif description:
        parts.append("The verified website describes it as: " + str(description["value"])[:300])
        used.append(description)
    if used:
        answered(QUESTIONS[0], " ".join(parts), used)
    else:
        unknown(QUESTIONS[0], "What the company does is not established: " + reason("description", "no registry or website description was found") + ".")

    # 2. Leaders.
    roles = [c for c in by_field.get("registered_role", []) if isinstance(c.get("value"), dict)]
    ceo = next((c for c in roles if c["value"].get("role_code") == "DAGL"), None)
    chair = next((c for c in roles if c["value"].get("role_code") == "LEDE"), None)
    if ceo or chair:
        text = " ".join(t for t in (f"Chief executive: {ceo['value'].get('name')}." if ceo else "",
                                    f"Board chair: {chair['value'].get('name')}." if chair else "") if t)
        answered(QUESTIONS[1], text, [ceo, chair])
    else:
        unknown(QUESTIONS[1], "No chief executive or board chair is established: " + reason("leadership", "no registered roles were found") + ".")

    # 3. Where it operates.
    address = first("registered_address")
    workplaces = by_field.get("registered_workplace", [])
    parts, used = [], []
    if address and isinstance(address["value"], dict):
        a = address["value"]
        line = ", ".join(x for x in [", ".join(a.get("adresse") or []), f"{a.get('postnummer', '')} {a.get('poststed', '')}".strip()] if x)
        parts.append(f"Registered business address: {line}.")
        used.append(address)
    if workplaces:
        parts.append(f"{len(workplaces)} registered workplace{'s' if len(workplaces) != 1 else ''}.")
        used.extend(workplaces)
    if used:
        answered(QUESTIONS[2], " ".join(parts), used)
    else:
        unknown(QUESTIONS[2], "Where it operates is not established: " + reason("locations", "no address or workplace was found") + ".")

    # 4. Latest filed numbers: one reporting period only (the latest of the company accounts).
    financial = [c for f, items in by_field.items() if f.startswith("financial_") for c in items
                 if c.get("scope") == "legal_entity" and c.get("reporting_period") and isinstance(c.get("value"), dict)]
    if financial:
        key = latest_period(financial)
        end = key[1]
        period = [c for c in financial if period_key(c) == key]
        labels = (("financial_revenue", "revenue"), ("financial_operating_profit", "operating result"), ("financial_net_profit", "annual result"),
                  ("financial_equity", "equity"), ("financial_assets", "total assets"))
        picked = [(next((c for c in period if c["field"] == f), None), label) for f, label in labels]
        picked = [(c, label) for c, label in picked if c]
        if picked:
            currency = picked[0][0]["value"].get("currency", "NOK")
            start = picked[0][0]["reporting_period"].get("fraDato")
            text = f"For {start} to {end} (company accounts): " + "; ".join(
                f"{label} {c['value'].get('currency', currency)} {nok(c['value'].get('amount'))}" for c, label in picked) + "."
            answered(QUESTIONS[3], text, [c for c, _ in picked])
        else:
            unknown(QUESTIONS[3], "No filed revenue, result, equity or assets figure is available for the latest period.")
    else:
        unknown(QUESTIONS[3], "No filed annual accounts are established: " + reason("financials", "no figures were found") + ".")

    # 5. Hiring.
    jobs = [c for c in by_field.get("job_posting", []) if isinstance(c.get("value"), dict)]
    if jobs:
        nav = [c for c in jobs if c["value"].get("status") == "active"]
        titles = [str(c["value"].get("title")) for c in jobs[:3] if c["value"].get("title")]
        text = (f"{len(nav)} job ad(s) marked active in NAV's official feed" if nav else "") + \
               (" and " if nav and len(jobs) > len(nav) else "") + \
               (f"{len(jobs) - len(nav)} posting(s) on the verified website" if len(jobs) > len(nav) else "") + \
               (": " + "; ".join(titles) if titles else "") + "."
        answered(QUESTIONS[4], text[0].upper() + text[1:], jobs)
    else:
        unknown(QUESTIONS[4], "No job ad was found in NAV's official job feed or on a verified company website; this does not show that the company is not hiring.")

    # 6. Website.
    site = first("official_website")
    if site:
        answered(QUESTIONS[5], f"Verified official website: {site['value']}.", [site])
    else:
        unknown(QUESTIONS[5], "No website is verified as this company's own: " + reason("website", "no candidate passed the identity check") + ".")

    # 7. Employees (registered count; never zero from absence).
    employees = first("employees")
    if employees and isinstance(employees.get("value"), int) and not isinstance(employees.get("value"), bool):
        answered(QUESTIONS[6], f"The official register reports {employees['value']} employee{'s' if employees['value'] != 1 else ''}.", [employees])
    else:
        unknown(QUESTIONS[6], "The number of employees is not established: " + reason("workforce", "the register gives no employee count") + ".")

    # 8. Changes.
    material = [c for c in envelope.get("changes", []) if c.get("material")]
    previous_run = (envelope.get("refresh") or {}).get("previous_run_id")
    if material:
        shown = material[:5]
        text = "; ".join(CHANGE_WORDS.get(c.get("type"), str(c.get("type"))) + f" ({c.get('field')})" for c in shown) + "."
        evidence_ids = sorted({e for c in shown for e in (c.get("current_evidence_ids") or c.get("previous_evidence_ids") or [])})
        answers.append({"question": QUESTIONS[7], "answerable": True, "answer": text[0].upper() + text[1:],
                        "claim_ids": [c["claim_id"] for c in shown if c.get("claim_id") in current_ids], "evidence_ids": evidence_ids})
    elif previous_run:
        answers.append({"question": QUESTIONS[7], "answerable": True, "answer": f"No material change since the previous run ({previous_run}).",
                        "claim_ids": [], "evidence_ids": []})
    else:
        unknown(QUESTIONS[7], "This is the first run for this company, so there is no earlier state to compare.")

    # 9. Dated public activity.
    posts = sorted([c for c in by_field.get("company_publication", []) if isinstance(c.get("value"), dict)],
                   key=lambda c: str(c["value"].get("published_on") or ""), reverse=True)[:3]
    if posts:
        answered(QUESTIONS[8], "; ".join(f"{c['value'].get('published_on')}: {c['value'].get('title')}" for c in posts) + ".", posts)
    else:
        unknown(QUESTIONS[8], "No dated public activity is established: " + reason("activity", "no dated company publication was found") + ".")

    # 10. Contact.
    labels = (("registered_phone", "phone (register)"), ("registered_mobile", "mobile (register)"), ("registered_email", "e-mail (register)"),
              ("website_email", "e-mail (website)"), ("website_phone", "phone (website)"))
    contacts = [(first(f), label) for f, label in labels if first(f)]
    if contacts:
        answered(QUESTIONS[9], "; ".join(f"{label}: {c['value']}" for c, label in contacts) + ".", [c for c, _ in contacts])
    else:
        unknown(QUESTIONS[9], "No contact detail is established: " + reason("contact", "none was found in the register or on a verified website") + ".")

    # 11. Distress flags from the register.
    flags = [c for c in (first("bankrupt"), first("liquidating")) if c is not None and isinstance(c.get("value"), bool)]
    raised = [c for c in flags if c["value"] is True]
    if raised:
        answered(QUESTIONS[10], "The official register flags " + " and ".join("bankruptcy" if c["field"] == "bankrupt" else "liquidation" for c in raised) + ".", raised)
    elif flags:
        answered(QUESTIONS[10], "The official register does not flag " + " or ".join("bankruptcy" if c["field"] == "bankrupt" else "liquidation" for c in flags) + ".", flags)
    else:
        unknown(QUESTIONS[10], "Registry distress flags are not established: " + reason("identity", "the register record was not available") + ".")
    return answers
