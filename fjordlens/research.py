"""Cross-company screening and cited single-company answers over finished envelopes.

The screening grammar is deliberately closed and every plan is inspectable: a query becomes explicit
(field, operator, value) filters plus an optional sort, or the agent abstains. Results and answers
carry citations (source URL, retrieval time, SHA-256) taken from the claims they rest on. Nothing is
computed from absent evidence: a company without the field never matches a numeric filter.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import re

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
    items = [c for c in _claims(envelope, field) if c.get("scope") == "legal_entity" and isinstance(c.get("value"), dict)]
    items.sort(key=lambda c: (c.get("reporting_period") or {}).get("tilDato", ""), reverse=True)
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
                    "content_sha256": e.get("content_sha256"), "quote": (e.get("claim_span") or "")[:300]}
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


TOPICS = (("financials", r"financ|account|revenue|income|profit|result|debt|asset|equity|regnskap|omsetning|numbers"),
          ("leadership", r"lead|role|ceo|board|chair|director|manage|leder|styre|runs"),
          ("locations", r"location|where|subunit|office|workplace|address|operat"),
          ("hiring", r"hir|job|vacanc|career|stilling|recruit|working"),
          ("activity", r"news|activ|publication|announc|nyhet"),
          ("online", r"web|site|social|contact|email|phone|linkedin|facebook"),
          ("identity", r"industry|purpose|describ|employee|founded|legal form|registered|what (?:does|do) (?:it|they|the company|this company) do|about (?:it|the company|this company)"))
TOPIC_FAMILIES = {"financials": {"financials", "filing_history"}, "leadership": {"leadership"}, "locations": {"locations"}, "hiring": {"hiring"},
                  "activity": {"activity"}, "online": {"website", "contact", "social_profiles", "description"}, "identity": {"identity", "description", "workforce"}}


def answer_profile(envelope, question):
    """Cited facts for one company: every fact carries its source URL, retrieval time and SHA-256."""
    q = str(question or "")
    if any(term in q.casefold() for term in ("sentiment", "glassdoor", "review", "buzz", "traffic", "fraud")):
        return {"organisation_number": envelope.get("organisation_number"), "question": q, "facts": [], "abstained": True,
                "unsupported_or_uncertain": [m for t, m in UNSUPPORTED_SCREEN_TERMS.items() if t in q.casefold()] or ["Not supported by qualified sources."]}
    topics = [name for name, pattern in TOPICS if re.search(pattern, q, re.I)] or [name for name, _ in TOPICS]
    families = set().union(*(TOPIC_FAMILIES[t] for t in topics))
    facts, gaps = [], []
    for claim in envelope.get("claims", []):
        if claim.get("family") in families and claim.get("availability") == "available":
            cited = citation(envelope, claim)
            if cited:
                facts.append({"claim": claim["field"], "value": claim["value"], "family": claim["family"], "scope": claim.get("scope"),
                              "reporting_period": claim.get("reporting_period"), "stale": bool(claim.get("stale")), **cited})
    for family in sorted(families):
        state = (envelope.get("availability") or {}).get(family) or {}
        if state.get("state") and state["state"] != "available":
            gaps.append(f"{family}: {state.get('state')} - {state.get('reason')}")
    return {"organisation_number": envelope.get("organisation_number"), "company_name": (envelope.get("legal_identity") or {}).get("name"),
            "question": q, "topics": topics, "facts": facts, "abstained": not facts, "unsupported_or_uncertain": gaps,
            "answer_policy": "Only source-linked facts are returned; missing evidence is reported as missing, never as zero."}
