"""Standard questions answered only from an envelope's claims (machine-readable `answers` block).

Every answer lists exactly the claims its text uses and their evidence ids. An unanswerable question says what is
unknown and why (from the family's availability reason) and never turns absence into zero or "no".
"""
from __future__ import annotations

from datetime import date
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
        key = period_key(claim)
        if key[0] and key[1]:
            counts[key] = counts.get(key, 0) + 1
    return max(counts, key=lambda key: (key[1], counts[key], [-ord(ch) for ch in key[0]])) if counts else None


def financial_period_claims(envelope, scope="legal_entity"):
    """Current claims from one complete period; never backfill an absent latest figure."""
    financial = [c for c in envelope.get("claims", []) if c.get("availability") == "available" and not c.get("stale")
                 and str(c.get("field", "")).startswith("financial_") and c.get("scope") == scope
                 and isinstance(c.get("value"), dict) and c.get("reporting_period")]
    key = latest_period(financial)
    return [c for c in financial if period_key(c) == key] if key else []


def amount(claim):
    try:
        value = Decimal(str(claim["value"]["amount"]))
        return value if value.is_finite() else None
    except (InvalidOperation, KeyError, TypeError):
        return None


def financial_insights(envelope):
    """Bounded interpretations and like-for-like annual comparisons, each citing every input."""
    sentences, limitations = [], []

    def add(text, used):
        sentences.append({"text": text, "claim_ids": sorted({c["id"] for c in used}), "stale": False})

    for scope, label in (("legal_entity", "company accounts"), ("consolidated_group", "consolidated group accounts")):
        latest = financial_period_claims(envelope, scope)
        if not latest:
            continue
        by_field = {c["field"]: c for c in latest}
        start, end = period_key(latest[0])
        period_label = f"{start} to {end} ({label})"
        operating, net, equity = (by_field.get(f"financial_{field}") for field in ("operating_profit", "net_profit", "equity"))
        if operating and net and amount(operating) is not None and amount(net) is not None:
            op, result = amount(operating), amount(net)
            op_word = "an operating profit" if op > 0 else "an operating loss" if op < 0 else "a break-even operating result"
            net_word = "an annual profit" if result > 0 else "an annual loss" if result < 0 else "a break-even annual result"
            add(f"For {period_label}, the company recorded {op_word} and {net_word}. These are filed accounting results for that period; they do not establish current cash flow.", [operating, net])
        if equity and amount(equity) is not None and amount(equity) < 0:
            add(f"At {end} ({label}), filed equity was negative: {equity['value'].get('currency', '')} {nok(amount(equity))}. Negative book equity is a balance-sheet warning; it does not by itself establish insolvency or bankruptcy.", [equity])

        # Only consecutive full years with matching start/end calendar days are compared.
        # This excludes short first accounts, changed year ends, restated partial years and gaps.
        try:
            current_start, current_end = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            continue
        candidates = [c for c in envelope.get("claims", []) if c.get("availability") == "available" and not c.get("stale")
                      and c.get("scope") == scope and str(c.get("field", "")).startswith("financial_") and c.get("reporting_period")]
        compared = set()
        for field, metric in (("financial_revenue", "Revenue"), ("financial_operating_profit", "Operating result"),
                              ("financial_net_profit", "Annual result")):
            current = by_field.get(field)
            if not current or amount(current) is None:
                continue
            previous = None
            for candidate in candidates:
                if candidate.get("field") != field or not isinstance(candidate.get("value"), dict):
                    continue
                if not current["value"].get("currency") or candidate["value"].get("currency") != current["value"].get("currency"):
                    continue
                try:
                    prior_start, prior_end = (date.fromisoformat(v) for v in period_key(candidate))
                except ValueError:
                    continue
                annual = 364 <= (current_end - current_start).days <= 365 and 364 <= (prior_end - prior_start).days <= 365
                consecutive = (current_start - prior_end).days == 1
                aligned = (current_start.month, current_start.day, current_end.month, current_end.day) == (prior_start.month, prior_start.day, prior_end.month, prior_end.day)
                if annual and consecutive and aligned and amount(candidate) is not None:
                    previous = candidate
                    break
            if not previous:
                continue
            now, before = amount(current), amount(previous)
            difference = now - before
            currency = current["value"]["currency"]
            prior_label = " to ".join(period_key(previous))
            change = f"increased by {currency} {nok(abs(difference))}" if difference > 0 else f"decreased by {currency} {nok(abs(difference))}" if difference < 0 else "was unchanged"
            percentage = f" ({difference / before * 100:+.1f}%)" if before > 0 and difference else ""
            add(f"{metric} {change}{percentage}: {currency} {nok(before)} in {prior_label} to {currency} {nok(now)} in {start} to {end} ({label}; calculated from comparable annual periods).", [previous, current])
            compared.add(field)
        if "financial_revenue" not in compared:
            limitations.append({"family": "financials", "scope": scope, "state": "not_available",
                                "text": f"Revenue growth is not established for {period_label}: comparable consecutive annual revenue figures in the same currency were not captured."})
    return sentences, limitations


def unchanged_refresh_note(envelope):
    previous = (envelope.get("refresh") or {}).get("previous_run_id")
    if not previous or any(c.get("material") for c in envelope.get("changes", [])):
        return None
    note = f"No material change was confirmed in this run compared with the previous run ({previous})."
    stale_count = sum(bool(c.get("stale")) for c in envelope.get("claims", []))
    if stale_count:
        noun = "fact" if stale_count == 1 else "facts"
        note += f" {stale_count} earlier {noun} could not be reconfirmed; this does not establish that they remained unchanged."
    return note


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
    if description:
        parts.append("The verified website describes it as (self-reported): " + str(description["value"])[:300])
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
    financial = financial_period_claims(envelope)
    financial_scope, financial_label = "legal_entity", "company accounts"
    if not financial:
        financial = financial_period_claims(envelope, "consolidated_group")
        financial_scope, financial_label = "consolidated_group", "consolidated group accounts; separate company figures were not captured"
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
            text = f"For {start} to {end} ({financial_label}): " + "; ".join(
                f"{label} {c['value'].get('currency', currency)} {nok(c['value'].get('amount'))}" for c, label in picked) + "."
            insights, _ = financial_insights(envelope)
            entity_ids = {c["id"] for c in claims if c.get("scope") == financial_scope}
            insights = [s for s in insights if set(s["claim_ids"]) <= entity_ids]
            used = [c for c, _ in picked]
            additional_ids = {i for s in insights for i in s["claim_ids"]} - {c["id"] for c in used}
            used.extend(c for c in claims if c["id"] in additional_ids)
            answered(QUESTIONS[3], " ".join([text] + [s["text"] for s in insights]), used)
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
        if len(jobs) > len(nav):
            text += " Website postings are company-published; current availability is not confirmed."
        if any(c["value"].get("posting_context") == "employment_agency" for c in jobs):
            text += " This employer is a recruitment or employment agency; positions may be with its clients."
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
        answers.append({"question": QUESTIONS[7], "answerable": True, "answer": unchanged_refresh_note(envelope),
                        "claim_ids": [], "evidence_ids": []})
    else:
        unknown(QUESTIONS[7], "This is the first run for this company, so there is no earlier state to compare.")

    # 9. Dated public activity.
    posts = sorted([c for c in by_field.get("company_publication", []) if isinstance(c.get("value"), dict)],
                   key=lambda c: str(c["value"].get("published_on") or ""), reverse=True)[:3]
    if posts:
        def publication_label(claim):
            return "third-party report" if claim.get("source_class") == "third_party_publication" else "company-published"
        answered(QUESTIONS[8], "; ".join(f"{c['value'].get('published_on')}: {c['value'].get('title')} ({c['value'].get('url', 'publication')}; {publication_label(c)})" for c in posts)
                 + ". The dates shown are publication dates.", posts)
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
        text = "The official register does not flag " + " or ".join("bankruptcy" if c["field"] == "bankrupt" else "liquidation" for c in flags) + "."
        used = list(flags)
        equity = next((c for c in financial_period_claims(envelope) if c.get("field") == "financial_equity"), None)
        if equity and amount(equity) is not None and amount(equity) < 0:
            text += f" Filed company equity was negative at {period_key(equity)[1]} ({equity['value'].get('currency', '')} {nok(amount(equity))}); that is a balance-sheet warning."
            used.append(equity)
        text += " These checks do not establish current solvency or rule out financial distress."
        answered(QUESTIONS[10], text, used)
    else:
        unknown(QUESTIONS[10], "Registry distress flags are not established: " + reason("identity", "the register record was not available") + ".")
    return answers
