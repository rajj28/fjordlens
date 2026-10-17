"""Cross-company screening and cited single-company answers over finished envelopes.

The screening grammar is deliberately closed and every plan is inspectable: a query becomes explicit
(field, operator, value) filters plus an optional sort, or the agent abstains. Results and answers
carry citations (source URL, retrieval time, SHA-256) taken from the claims they rest on. Nothing is
computed from absent evidence: a company without the field never matches a numeric filter.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import re

from .answers import QUESTIONS, build_answers, financial_insights, financial_period_claims

UNSUPPORTED_SCREEN_TERMS = {
    "sentiment": "sentiment is not qualified: no labelled Norwegian evaluation corpus has been run",
    "glassdoor": "Glassdoor data is not available through a permitted connector",
    "linkedin": "LinkedIn-derived data is not available through a permitted connector",
    "traffic": "website traffic is not available through a qualified provider",
    "review": "review and rating data is not available through a qualified provider",
    "buzz": "social buzz is not available through a qualified provider",
    "popular": "popularity is not measured by any qualified source",
    "fraud": "fraud cannot be established from the collected public records",
    "without a website": "a missing or unverified website does not prove a company has no website",
    "no website": "a missing or unverified website does not prove a company has no website",
    "velocity": "hiring velocity needs a time series this collection does not hold",
    "culture": "workplace culture is not measured by any qualified source",
}
OPERATORS = {"more than": ">", "over": ">", "above": ">", "greater than": ">", "at least": ">=", "minimum": ">=",
             "fewer than": "<", "less than": "<", "under": "<", "below": "<", "at most": "<=", "maximum": "<="}
LEGAL_FORMS = "asa|as|enk|nuf|ans|da|sa|sti|brl|ba|ks|iks|fli|esek|sam|spa|kf|bbl"


def _number(text, unit):
    amount = Decimal(text.replace(" ", "").replace(",", "."))
    unit = (unit or "").casefold()
    return amount * (1_000_000_000 if unit in {"billion", "bn", "mrd", "milliard", "milliarder"} else 1_000_000 if unit in {"million", "m", "mill", "mnok", "millioner"} else 1)


FILLER = {"companies", "company", "firms", "firm", "businesses", "business", "organisations", "organizations", "entities", "show",
          "list", "find", "give", "get", "me", "all", "the", "a", "an", "with", "and", "or", "that", "which", "who", "are", "is",
          "in", "of", "for", "to", "have", "has", "having", "registered", "please", "whose", "their", "its", "norwegian",
          "norway", "norge", "located", "based", "where", "nok"}


def parse_screen_query(query):
    """Closed grammar -> inspectable plan. Unknown or unsupported criteria make the plan non-executable: a query is
    never run with part of its criteria silently ignored."""
    text = " ".join(str(query or "").strip().split())
    lower = text.lower()
    filters, spans = [], []
    unsupported = sorted({message for term, message in UNSUPPORTED_SCREEN_TERMS.items() if term in lower})

    def find(*patterns):
        for pattern in patterns:
            match = re.search(pattern, lower)
            if match:
                spans.append(match.span())
                return match
        return None

    municipality = find(r"\b(?:in|located in|municipality(?:\s+is|\s*=)?)\s+([a-zæøåéü .'-]+?)(?=\s+(?:with|and|having|that|where|top|sorted|by)\b|$)")
    if municipality and municipality.group(1).strip() not in {"norway", "norge"}:
        filters.append({"field": "municipality", "operator": "eq", "value": municipality.group(1).strip().upper(), "evidence": "registered_address"})
    legal_form = find(rf"\b(?:legal\s+form|organisation\s+form|organization\s+form|form)\s*(?:is|=)?\s*({LEGAL_FORMS})\b")
    if legal_form:
        filters.append({"field": "legal_form", "operator": "eq", "value": legal_form.group(1).upper(), "evidence": "legal_form"})
    employees = find(r"\b(more than|over|above|greater than|at least|fewer than|less than|under|below|at most)\s+(\d+)\s+(?:registered\s+)?(?:employees?|ansatte)\b",
                     r"\b(?:employees?|ansatte)\s*(>=|<=|>|<|=)\s*(\d+)\b")
    if employees:
        filters.append({"field": "employees", "operator": OPERATORS.get(employees.group(1), employees.group(1)), "value": int(employees.group(2)), "evidence": "employees"})
    revenue = find(r"\b(?:revenue|turnover|omsetning)\s*(>=|<=|>|<|=|more than|over|above|greater than|at least|fewer than|less than|under|below|at most)\s*(?:nok\s*)?([\d][\d ,.]*)\s*(billion|bn|million|m|mill|mnok|mrd)?\b",
                   r"\b(more than|over|above|greater than|at least|fewer than|less than|under|below|at most)\s*(?:nok\s*)?([\d][\d ,.]*)\s*(billion|bn|million|m|mill|mnok|mrd)?\s+(?:in\s+)?(?:revenue|turnover|omsetning)\b")
    if revenue:
        try:
            amount = _number(revenue.group(2).strip(" ,."), revenue.group(3))
            filters.append({"field": "revenue", "operator": OPERATORS.get(revenue.group(1), revenue.group(1)),
                            "value": int(amount) if amount == amount.to_integral_value() else float(amount), "evidence": "financial_revenue"})
        except InvalidOperation:
            unsupported.append("revenue amount could not be read")
    if find(r"\b(unprofitable|loss[- ]making|negative annual result)\b"):
        filters.append({"field": "annual_result", "operator": "<", "value": 0, "evidence": "financial_net_profit"})
    elif find(r"\b(profitable|positive annual result)\b"):
        filters.append({"field": "annual_result", "operator": ">", "value": 0, "evidence": "financial_net_profit"})
    if find(r"\b(?:with|has|have|having)\s+(?:an?\s+)?(?:verified\s+|official\s+)?website\b"):
        filters.append({"field": "website", "operator": "present", "value": True, "evidence": "official_website"})
    if find(r"\b(?:with|has|have|having)\s+(?:filed\s+|annual\s+)?accounts\b"):
        filters.append({"field": "financials", "operator": "available", "value": True, "evidence": "financial_revenue"})
    if find(r"\b(?:hiring|with (?:open )?(?:jobs|job ads|vacancies))\b"):
        filters.append({"field": "hiring", "operator": "present", "value": True, "evidence": "job_posting"})
    industry = find(r"\bindustry(?:\s+contains|\s+is|\s*=)?\s+[\"']([^\"']+)[\"']")
    if industry:
        filters.append({"field": "industry", "operator": "contains", "value": industry.group(1).casefold(), "evidence": "industry"})
    sort = None
    top = find(r"\btop\s+(\d+)\s+(?:companies\s+)?by\s+(revenue|employees)\b")
    if top:
        sort = {"field": top.group(2), "direction": "desc", "limit": max(1, min(int(top.group(1)), 100))}
    residual = list(lower)
    for start, stop in spans:
        residual[start:stop] = " " * (stop - start)
    leftover = [w for w in re.findall(r"[a-zæøåéü0-9]+", "".join(residual)) if w not in FILLER and not w.isdigit()]
    if leftover and not unsupported:
        unsupported.append("criterion not supported by the screening grammar: " + " ".join(leftover[:8]))
    return {"version": "closed_company_screen_v1", "query": text, "filters": filters, "sort": sort, "unsupported": unsupported,
            "executable": bool(filters or sort) and not unsupported}


def _claims(envelope, field):
    return [c for c in envelope.get("claims", []) if c.get("field") == field and c.get("availability") == "available" and not c.get("stale")]


def _latest_amount(envelope, field):
    items = [c for c in financial_period_claims(envelope) if c.get("field") == field and c["value"].get("currency") == "NOK"]
    if not items:
        return None, None
    try:
        return float(Decimal(str(items[0]["value"]["amount"]))), items[0]
    except (InvalidOperation, KeyError, TypeError):
        return None, None


def screen_value(envelope, field):
    """(value, supporting claim or None) for one screen field."""
    first = lambda name: next(iter(_claims(envelope, name)), None)  # noqa: E731
    if field == "municipality":
        claim = first("registered_address")
        return ((claim["value"].get("kommune") if isinstance(claim["value"], dict) else None) if claim else None), claim
    if field == "legal_form":
        claim = first("legal_form")
        return (claim["value"] if claim else None), claim
    if field == "employees":
        claim = first("employees")
        return (claim["value"] if claim else None), claim
    if field == "revenue":
        return _latest_amount(envelope, "financial_revenue")
    if field == "annual_result":
        return _latest_amount(envelope, "financial_net_profit")
    if field == "website":
        claim = first("official_website")
        return bool(claim), claim
    if field == "financials":
        claim = first("financial_revenue") or first("financial_assets")
        return bool(claim), claim
    if field == "hiring":
        claim = first("job_posting")
        return bool(claim), claim
    if field == "industry":
        claim = first("industry")
        value = claim["value"] if claim else None
        return (" ".join(str(value.get(k, "")) for k in ("kode", "beskrivelse")) if isinstance(value, dict) else value), claim
    return None, None


def _matches(actual, operator, expected):
    if operator == "eq":
        return actual is not None and str(actual).casefold() == str(expected).casefold()
    if operator in {"present", "available"}:
        return bool(actual) is bool(expected)
    if operator == "contains":
        return actual is not None and str(expected).casefold() in str(actual).casefold()
    if actual is None:
        return False  # Missing is never zero: absent evidence cannot satisfy a numeric filter.
    return {">": actual > expected, ">=": actual >= expected, "<": actual < expected, "<=": actual <= expected, "=": actual == expected}[operator]


def citation(envelope, claim):
    if not claim:
        return None
    evidence = {e["id"]: e for e in envelope.get("evidence", [])}
    for eid in claim.get("evidence_ids", []):
        e = evidence.get(eid)
        if e:
            return {"claim_id": claim["id"], "field": claim["field"], "source_url": e.get("source_url"), "retrieved_at": e.get("retrieved_at"),
                    "content_sha256": e.get("content_sha256"), "quote": (e.get("claim_span") or "")[:300], "evidence_id": eid}
    return None


def screen_profiles(envelopes, query):
    plan = parse_screen_query(query)
    if not plan["executable"]:
        return {"query": query, "plan": plan, "results": [], "result_count": 0, "abstained": True,
                "reason": "; ".join(plan["unsupported"]) or "No supported criterion was recognized."}
    results = []
    seen = set()
    for envelope in envelopes:
        org = envelope.get("organisation_number")
        if org in seen:
            continue
        seen.add(org)
        support, ok = [], True
        for item in plan["filters"]:
            value, claim = screen_value(envelope, item["field"])
            if not _matches(value, item["operator"], item["value"]):
                ok = False
                break
            support.append(claim)
        if not ok:
            continue
        revenue, revenue_claim = _latest_amount(envelope, "financial_revenue")
        employees, employees_claim = screen_value(envelope, "employees")
        if plan["sort"]:
            support.append(revenue_claim if plan["sort"]["field"] == "revenue" else employees_claim)
        municipality, _ = screen_value(envelope, "municipality")
        results.append({"organisation_number": org, "name": (envelope.get("legal_identity") or {}).get("name"), "municipality": municipality,
                        "employees": employees, "revenue": revenue, "annual_result": _latest_amount(envelope, "financial_net_profit")[0],
                        "citations": [c for c in (citation(envelope, claim) for claim in support) if c]})
    sort = plan["sort"]
    if sort:
        results.sort(key=lambda r: (r.get(sort["field"]) is None, -(r.get(sort["field"]) or 0), r["organisation_number"] or ""))
        results = results[:sort["limit"]]
    else:
        results.sort(key=lambda r: r["organisation_number"] or "")
    return {"query": query, "plan": plan, "results": results, "result_count": len(results), "abstained": False}


# These regex routes are exported to the static report, so hosted and downloaded answers agree.
# Use syntax shared by Python and JavaScript; exact standard questions take precedence.
ONLINE_QUESTION = "What is its online presence?"
OVERVIEW_QUESTION = "Give me a company overview."
ANSWER_ROUTES = [
    (QUESTIONS[10], r"\b(?:distress|bankrupt\w*|insolven\w*|solven\w*|liquidat\w*|financial risk)\b"),
    (QUESTIONS[7], r"\b(?:chang\w*|refresh\w*|since the last|since last|updates?)\b"),
    (QUESTIONS[6], r"\b(?:employees?|headcount|workforce|how many people|employs?|ansatte)\b"),
    (QUESTIONS[1], r"\b(?:lead\w*|roles?|ceo|board|chair\w*|director\w*|manag\w*|leder|styre|runs?)\b"),
    (QUESTIONS[2], r"\b(?:locations?|where|subunits?|offices?|workplaces?|address\w*|operates?|operating locations)\b"),
    (QUESTIONS[4], r"\b(?:hir\w*|jobs?|vacanc\w*|careers?|stilling\w*|recruit\w*|working)\b"),
    (QUESTIONS[8], r"\b(?:news|activity|publications?|announc\w*|nyhet\w*)\b"),
    (QUESTIONS[9], r"\b(?:contact\w*|e-?mail|phone|telephone)\b"),
    (ONLINE_QUESTION, r"\b(?:social\w*|linkedin|facebook|instagram|online presence)\b"),
    (QUESTIONS[5], r"\b(?:websites?|web sites?|domains?)\b"),
    (QUESTIONS[3], r"\b(?:financ\w*|accounts?|revenue|income|profit\w*|results?|debt|assets?|equity|regnskap|omsetning|numbers|margins?|growth|growing|trends?)\b"),
    (QUESTIONS[0], r"\b(?:industry|purpose|describ\w*|business|what (?:does|do) .+ do)\b"),
    (OVERVIEW_QUESTION, r"\b(?:overview|summari[sz]e|summary|about (?:it|them|the company|this company))\b"),
]
UNSUPPORTED_ANSWER_ROUTES = [
    (r"\b(?:sentiment|glassdoor|reviews?|buzz|traffic|fraud\w*)\b", "This is not established by the qualified sources collected for this company."),
    (r"\b(?:predict\w*|forecast\w*|will it|should I invest|buy (?:its |the )?shares|share price)\b", "The captured public records do not support predictions or investment recommendations."),
]


def answer_question_keys(question):
    q = str(question or "").strip()
    if any(re.search(pattern, q, re.I) for pattern, _ in UNSUPPORTED_ANSWER_ROUTES):
        return []
    known = list(QUESTIONS) + [ONLINE_QUESTION, OVERVIEW_QUESTION]
    exact = next((item for item in known if item.casefold().rstrip("?. ") == q.casefold().rstrip("?. ")), None)
    if exact:
        return [exact]
    found = [key for key, pattern in ANSWER_ROUTES if re.search(pattern, q, re.I)]
    if QUESTIONS[10] in found:
        found = [key for key in found if key != QUESTIONS[3]]
    return list(dict.fromkeys(found))


def answer_profile(envelope, question):
    """Direct, current answers plus the exact supporting records and source citations."""
    q = str(question or "")
    keys = answer_question_keys(q)
    blocked = [message for pattern, message in UNSUPPORTED_ANSWER_ROUTES if re.search(pattern, q, re.I)]
    standard = {a["question"]: a for a in build_answers(envelope)}
    picked = [standard[key] for key in keys if key in standard]
    if ONLINE_QUESTION in keys or OVERVIEW_QUESTION in keys:
        from .synthesis import summarize
        summary = summarize(envelope)
        if ONLINE_QUESTION in keys:
            online = [sentence for section in summary["sections"] if section["key"] == "online_presence" for sentence in section["sentences"]]
            picked.append({"answer": " ".join(s["text"] for s in online) or "No verified online presence is established by the captured sources.",
                           "answerable": bool(online), "claim_ids": sorted({cid for s in online for cid in s["claim_ids"]}), "evidence_ids": []})
        if OVERVIEW_QUESTION in keys:
            picked.append({"answer": summary["brief"]["text"] or "The captured sources do not establish a company overview.",
                           "answerable": bool(summary["brief"]["text"]), "claim_ids": summary["brief"]["claim_ids"], "evidence_ids": []})
    gaps = blocked or [a["answer"] for a in picked if not a["answerable"]]
    if QUESTIONS[3] in keys:
        _, limitations = financial_insights(envelope)
        gaps.extend(item["text"] for item in limitations if item["scope"] == "legal_entity")
    if not picked:
        message = "; ".join(blocked) or "This question is not supported by the captured company facts. Ask about its business, leadership, locations, filed finances, hiring, website, employees, changes, activity or contact details."
        gaps = [message]
        picked = [{"answer": message, "answerable": False, "claim_ids": [], "evidence_ids": []}]
    used_ids = {cid for item in picked for cid in item["claim_ids"]}
    claims = [c for c in envelope.get("claims", []) if c.get("id") in used_ids and c.get("availability") == "available" and not c.get("stale")]
    citations = [c for c in (citation(envelope, claim) for claim in claims) if c]
    cited_by_id = {c["claim_id"]: c for c in citations}
    facts = [{"claim": c["field"], "value": c["value"], "family": c["family"], "scope": c.get("scope"),
              "reporting_period": c.get("reporting_period"), "stale": False, **cited_by_id[c["id"]]} for c in claims if c["id"] in cited_by_id]
    sentences = [{"text": a["answer"], "claim_ids": a["claim_ids"], "stale": False} for a in picked]
    evidence_ids = sorted({eid for c in claims for eid in c.get("evidence_ids", [])} | {eid for a in picked for eid in a.get("evidence_ids", [])})
    families = {c["family"] for c in claims}
    answerable = any(a["answerable"] for a in picked)
    return {"organisation_number": envelope.get("organisation_number"), "company_name": (envelope.get("legal_identity") or {}).get("name"),
            "question": q, "topics": keys, "answer": " ".join(s["text"] for s in sentences), "answerable": answerable,
            "sentences": sentences, "claim_ids": sorted(used_ids), "evidence_ids": evidence_ids, "citations": citations,
            "facts": facts, "claims": claims, "abstained": not answerable, "unsupported_or_uncertain": list(dict.fromkeys(gaps)),
            "availability": {f: (envelope.get("availability") or {}).get(f, {}) for f in sorted(families)},
            "changes": envelope.get("changes", []) if QUESTIONS[7] in keys else [], "method": "grounded_answers_v2",
            "answer_policy": "Only current source-linked claims support the answer; missing evidence is reported as missing, never as zero. Filed periods and company/group scope are kept separate."}
