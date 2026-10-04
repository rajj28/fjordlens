"""Optional website-candidate nomination through Brave's Web Search API.

Inactive unless the evaluator supplies `BRAVE_SEARCH_API_KEY` (a key tied to our own account cannot be used in
official runs). Search results are candidates only, never evidence: responses are not stored, the request log keeps
the endpoint without the query, and every nominated site must pass the unchanged identity gate on pages we fetch
ourselves. Queries go org number first (a page that states the number is the strongest proof), then legal name with
municipality; at most one follow-up query per company.
"""
from __future__ import annotations

import math
import os
from urllib.parse import urlencode, urlsplit

from . import discovery, gate

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
KEY_ENVS = ("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY")


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


def queries(entity):
    org = str(entity.get("organisasjonsnummer") or "")
    spaced = f"{org[:3]} {org[3:6]} {org[6:]}" if len(org) == 9 else org
    words = [t for t in gate.name_tokens(entity.get("navn") or "")]
    name = " ".join(words)
    town = str(((entity.get("forretningsadresse") or {}).get("poststed")) or "").title()
    first = {"kind": "search_orgnr", "q": f'"{org}" OR "{spaced}"', "needs_name_in_host": False}
    second = {"kind": "search_name", "q": f'"{name}" {town}'.strip(), "needs_name_in_host": True}
    return [first, second] if name else [first]


def usable(url, entity, needs_name_in_host, exclude):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    host = parts.hostname.lower().removeprefix("www.")
    domain = gate.registered_domain(url)
    if domain in exclude or any(host == h or host.endswith("." + h) for h in gate.NON_SITE_HOSTS) or discovery.social_platform(url):
        return False
    if needs_name_in_host:
        # The domain must carry the name's most distinctive (longest non-generic) word, not just any word.
        distinctive = sorted((t for t in gate.name_tokens(entity.get("navn") or "") if t not in discovery.GENERIC and len(t) >= 4),
                             key=len, reverse=True)
        label = domain.split(".")[0].replace("-", "")
        return bool(distinctive) and distinctive[0] in label
    return True


def nominate(fetcher, entity, exclude=(), limit=3):
    """Ranked candidate sites from search, as discovery candidates (origin search_orgnr / search_name)."""
    key = api_key()
    if not key:
        return []
    org = entity.get("organisasjonsnummer")
    exclude = set(exclude)
    found = []
    for query in queries(entity):
        url = ENDPOINT + "?" + urlencode({"q": query["q"], "country": "no", "count": 10, "safesearch": "strict"})
        r = fetcher.get(url, org, headers={"X-Subscription-Token": key, "Accept": "application/json"},
                        cost=cost_per_query(), store=False)
        if r.state != "available":
            continue
        try:
            results = ((r.json() or {}).get("web") or {}).get("results") or []
        except ValueError:
            continue
        for item in results:
            link = item.get("url") if isinstance(item, dict) else None
            if not isinstance(link, str) or not usable(link, entity, query["needs_name_in_host"], exclude):
                continue
            exclude.add(gate.registered_domain(link))
            parts = urlsplit(link)
            found.append({"url": f"{parts.scheme}://{parts.netloc}/", "origin": query["kind"], "search_rank": len(found) + 1})
            if len(found) >= limit:
                return found
        if found:
            return found  # Only spend the follow-up query when the org-number query nominated nothing.
    return found
