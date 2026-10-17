"""Optional website-candidate nomination through Brave's Web Search API.

Inactive unless the evaluator supplies `BRAVE_SEARCH_API_KEY` (a key tied to our own account cannot be used in
official runs). Search results are candidates only, never evidence: responses are not stored, the request log keeps
the endpoint without the query, and every nominated site must pass the unchanged identity gate on pages we fetch
ourselves. Queries go org number first, then the legal name and a registered trading name,
then an ownership-page query. The caller stops after a verified site; at most four queries
share the fetcher's request, deadline and external-cost budget with all other connectors.
"""
from __future__ import annotations

import math
import os
import re
from urllib.parse import urlencode, urlsplit

from . import discovery, gate

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
KEY_ENVS = ("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY")
MAX_QUERIES = 4
MAX_CANDIDATES_PER_QUERY = 3
MAX_PROOF_URLS = 3


def api_key():
    return next((os.environ[k].strip() for k in KEY_ENVS if os.environ.get(k, "").strip()), "")


def enabled():
    return bool(api_key())


def cost_per_query():
    try:
        value = float(os.environ.get("BRAVE_COST_PER_REQUEST_USD") or 0.005)
    except ValueError:
        return 0.005
    return value if math.isfinite(value) and value >= 0 else 0.005


def _query_name(value):
    # Preserve Norwegian spelling. Folding a quoted legal name can eliminate the
    # exact indexed page, e.g. VIKØREN becomes the different phrase VIKOREN.
    return " ".join(re.sub(r'["\\\x00-\x1f]', " ", str(value or "")).split())[:200]


def queries(entity, *, brands=(), max_queries=MAX_QUERIES):
    org = str(entity.get("organisasjonsnummer") or "")
    spaced = f"{org[:3]} {org[3:6]} {org[6:]}" if len(org) == 9 else org
    name = _query_name(entity.get("navn"))
    town = _query_name((entity.get("forretningsadresse") or {}).get("poststed")).title()
    out = []
    if re.fullmatch(r"\d{9}", org):
        out.append({"kind": "search_orgnr", "query_kind": "organisation_number", "q": f'"{org}" OR "{spaced}"'})
    if name:
        out.append({"kind": "search_name", "query_kind": "legal_name", "q": f'"{name}" {town}'.strip()})
    for brand in discovery.registered_brands(entity, brands, limit=1):
        out.append({"kind": "search_name", "query_kind": "registered_workplace_name",
                    "q": f'"{_query_name(brand)}" {town}'.strip()})
    if re.fullmatch(r"\d{9}", org):
        # Company directories often occupy the entire first result page. A bounded
        # second number query looks specifically for ownership pages without them.
        out.append({"kind": "search_orgnr", "query_kind": "ownership_page",
                    "q": f'("{org}" OR "{spaced}") (kontakt OR personvern OR vilkår OR "legal notice") '
                         '-site:proff.no -site:purehelp.no -site:brreg.no -site:1881.no'})
    return [{**q, "needs_name_in_host": False} for q in out[:max(0, min(MAX_QUERIES, max_queries))]]


def usable(url, entity, needs_name_in_host, exclude):
    """Validate nomination URLs; host spelling is deliberately not identity proof.

    ``needs_name_in_host`` remains a compatibility argument. Exact-name searches
    can legitimately return acronym or trading-name domains. The fetched pages,
    never this search result, still have to satisfy the unchanged identity gate.
    """
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return False
        parts.port  # Reject malformed ports before passing the URL to the fetcher.
    except ValueError:
        return False
    host = parts.hostname.lower().removeprefix("www.")
    domain = gate.registered_domain(url)
    if domain in exclude or any(host == h or host.endswith("." + h) for h in gate.NON_SITE_HOSTS) or discovery.social_platform(url):
        return False
    return True


def _proof_priority(url):
    path = urlsplit(url).path.casefold()
    for rank, terms in enumerate((
        ("legal-notice", "legal_notice", "impressum", "contact-information"),
        ("personvern", "privacy", "vilkar", "vilkår", "terms"),
        ("kontakt", "contact"),
        ("om-oss", "about"),
    )):
        if any(term in path for term in terms):
            return rank
    return 4


def nomination_batches(fetcher, entity, exclude=(), limit=3, allow_verification=False, *, brands=(), max_queries=MAX_QUERIES):
    """Yield one transient nomination batch per query, so verification decides whether to search again.

    The caller stops iteration as soon as a candidate is verified. A deep search result may
    nominate a proof page on a previously tried domain; it is still fetched and gated normally.
    """
    key = api_key()
    limit = max(0, min(MAX_CANDIDATES_PER_QUERY, limit))
    if not key or not limit:
        return
    org = entity.get("organisasjonsnummer")
    exclude = set(exclude)
    nominated_urls = set()
    for query in queries(entity, brands=brands, max_queries=max_queries):
        found = []
        url = ENDPOINT + "?" + urlencode({"q": query["q"], "country": "no", "count": 10, "safesearch": "strict"})
        r = fetcher.get(url, org, headers={"X-Subscription-Token": key, "Accept": "application/json"},
                        cost=cost_per_query(), store=False)
        if r.state != "available":
            # A missing/denied key, exhausted budget or rate limit is not fixed by
            # another query. The fetcher has already applied its bounded retry policy.
            if r.state == "blocked" or str(getattr(r, "error", "") or "").startswith(("Budget:", "Cutoff:")):
                return
            continue
        try:
            payload = r.json()
        except ValueError:
            continue
        web = payload.get("web") if isinstance(payload, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            continue
        for rank, item in enumerate(results[:10], 1):
            link = item.get("url") if isinstance(item, dict) else None
            if not isinstance(link, str):
                continue
            if not usable(link, entity, False, ()):
                continue
            parts = urlsplit(link)
            has_proof_path = parts.path not in ("", "/") or bool(parts.query)
            allowed_exclude = () if allow_verification and has_proof_path else exclude
            if not usable(link, entity, query["needs_name_in_host"], allowed_exclude):
                continue
            domain = gate.registered_domain(link)
            if link in nominated_urls:
                continue
            candidate = next((c for c in found if gate.registered_domain(c["url"]) == domain), None)
            if candidate is not None:
                # Do not discard a legal/contact result just because the same query
                # listed its homepage first. Cross-host paths remain separate proof.
                if has_proof_path and gate.host(link) == gate.host(candidate["url"]):
                    proofs = candidate.setdefault("verification_urls", [])
                    if link not in proofs:
                        proofs.append(link)
                        proofs.sort(key=_proof_priority)
                        del proofs[MAX_PROOF_URLS:]
                        candidate["verification_url"] = proofs[0]
                continue
            if len(found) >= limit:
                continue
            nominated_urls.add(link)
            candidate = {"url": f"{parts.scheme}://{parts.netloc}/", "origin": query["kind"],
                         "search_rank": rank, "search_query_kind": query["query_kind"]}
            if has_proof_path:
                candidate["verification_url"] = link
                candidate["verification_urls"] = [link]
            found.append(candidate)
        for candidate in found:
            nominated_urls.update(candidate.get("verification_urls", []))
        yield found


def nominate(fetcher, entity, exclude=(), limit=3):
    """Compatibility helper: first non-empty batch. Live research uses nomination_batches."""
    for found in nomination_batches(fetcher, entity, exclude=exclude, limit=limit):
        if found:
            return found
    return []
