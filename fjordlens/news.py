"""Independent, fetched news evidence; licensed search only nominates URLs.

The connector is deliberately conservative: a publication needs its own headline and
publication date, a single article body, and the exact legal name corroborated there
by a labelled organisation number, registered address, or company-domain link.
Search titles, snippets and dates never become evidence. All requests use the shared
Fetcher budget and its robots, public-address, TLS, redirect and snapshot policies.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from . import articles, discovery, gate, search
from .core import org_number
from .html import clean, parse_html
from .net import FetchError, safe_url

MAX_CANDIDATES = 3
MAX_QUERIES = 2
MAX_NOMINATIONS = 5
SOURCE_CLASS = "third_party_publication"
METHOD = "independent_news_v1"
SKIP = {"script", "style", "template", "noscript", "nav", "aside", "footer", "form", "svg"}
NOISE = re.compile(r"(?:^|[\s_-])(?:footer|copyright|comment\w*|review\w*|related\w*|recommend\w*|sidebar|share|author|byline|publisher)(?:$|[\s_-])", re.I)
NON_ARTICLE = re.compile(r"^/(?:news|nyheter|aktuelt|blogg?|press|presse|search|sok|sitemap|category|tag|author)/?$|/(?:products?|collections?|shop|cart|checkout)(?:/|$)", re.I)


def _phrase(text, name):
    """Ordered whole-token phrase; a generic subset of a company name is insufficient."""
    needle, hay = gate.tokens(name), gate.tokens(text)
    return bool(needle) and any(hay[i:i + len(needle)] == needle for i in range(len(hay) - len(needle) + 1))


def _url_key(url):
    p = urlsplit(url)
    return (gate.host(url), articles.canonical_path(p.path).rstrip("/"), articles.functional_query(p.query))


def _clean_url(url):
    try:
        safe = safe_url(url)
        p = urlsplit(safe)
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
    except (FetchError, ValueError, TypeError, UnicodeError):
        return None


def _allowed_url(url, company_domains=()):
    url = _clean_url(url)
    if not url:
        return None
    p = urlsplit(url)
    domain, host = gate.registered_domain(url), gate.host(url)
    if (not p.path.strip("/") or NON_ARTICLE.search(p.path)
            or p.path.lower().endswith(articles.FILE_SUFFIXES)
            or domain in company_domains
            or any(host == h or host.endswith("." + h) for h in gate.NON_SITE_HOSTS)):
        return None
    return url


def entity_context(entity):
    """The public registry fields needed to reproduce identity binding from saved bytes."""
    address = entity.get("forretningsadresse") or {}
    return {"organisasjonsnummer": str(entity.get("organisasjonsnummer") or ""),
            "navn": str(entity.get("navn") or ""), "hjemmeside": str(entity.get("hjemmeside") or ""),
            "epostadresse": str(entity.get("epostadresse") or ""),
            "forretningsadresse": {k: address[k] for k in ("adresse", "postnummer", "poststed") if k in address}}


def declared_domains(entity):
    domains = set()
    url = discovery.normalize_url(entity.get("hjemmeside"))
    if url and not discovery.social_platform(url):
        domains.add(gate.registered_domain(url))
    email = str(entity.get("epostadresse") or "").strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain and domain not in discovery.CONSUMER_EMAIL and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        domains.add(gate.registered_domain(domain))
    return domains


def company_domains(profile, entity):
    """Current gate-verified company domains, for article-link identity and audit.

    Registry declarations alone can name an accountant, parent or management portal;
    they exclude possible own-site candidates but do not prove news identity.
    """
    data = profile.data if hasattr(profile, "data") else profile
    return {gate.registered_domain(c["value"]) for c in data.get("claims", [])
               if c.get("field") == "official_website" and isinstance(c.get("value"), str)
               and c.get("availability") == "available" and c.get("scope") == "legal_entity"
               and not c.get("stale") and not urlsplit(c["value"]).path.strip("/")}


class _Content(HTMLParser):
    """Bounded visible article paragraphs, retaining their literal source spans."""

    def __init__(self, source, page_url):
        super().__init__(convert_charrefs=True)
        self.source, self.page_url = source, page_url
        self.lines = [0] + [m.end() for m in re.finditer("\n", source)]
        self.stack, self.blocks, self.titles = [], [], []
        self.article_count = self.main_count = 0

    def here(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = (any(e["hidden"] for e in self.stack) or tag in SKIP or "hidden" in a
                  or a.get("aria-hidden", "").lower() == "true"
                  or a.get("role", "").lower() in {"navigation", "complementary", "contentinfo"}
                  or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", a.get("style", ""), re.I))
                  or bool(NOISE.search(a.get("class", "") + " " + a.get("id", ""))))
        if tag == "meta" and not hidden and (a.get("property") or a.get("name") or "").lower() == "og:title":
            self.titles.append({"text": clean(a.get("content")), "span": self.get_starttag_text()})
        if tag in articles.VOID:
            return
        if len(self.stack) >= 120:
            # Excessive nesting cannot provide auditable article evidence.
            raise ValueError("Article markup exceeds nesting limit")
        scope = "article" if tag == "article" or any(e["scope"] == "article" for e in self.stack) else (
            "main" if tag == "main" or any(e["scope"] == "main" for e in self.stack) else "")
        if not hidden:
            self.article_count += int(tag == "article")
            self.main_count += int(tag == "main")
        entry = {"tag": tag, "start": self.here(), "text": [], "size": 0, "links": [],
                 "hidden": hidden, "scope": scope, "url": urljoin(self.page_url, a.get("href", "")) if tag == "a" else None}
        self.stack.append(entry)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in articles.VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if any(e["hidden"] for e in self.stack):
            return
        for e in self.stack:
            if e["size"] < 24000:
                e["text"].append(data)
                e["size"] += len(data)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if index is None:
            return
        end = self.source.find(">", self.here())
        end = len(self.source) if end < 0 else end + 1
        entries = self.stack[index:]
        del self.stack[index:]
        for e in reversed(entries):
            if e["hidden"]:
                continue
            text, span = clean(" ".join(e["text"])), self.source[e["start"]:end]
            if e["tag"] == "a":
                for parent in self.stack + entries[:entries.index(e)]:
                    if len(parent["links"]) < 100:
                        parent["links"].append({"url": e["url"], "text": text})
            if e["tag"] in {"title", "h1"} and 8 <= len(text) <= 300 and len(span) <= 3000:
                self.titles.append({"text": text, "span": span})
            if (e["scope"] and e["tag"] in {"p", "li", "address"}
                    and not any(parent["tag"] == "header" for parent in self.stack + entries[:entries.index(e)])
                    and 10 <= len(text) <= 1800 and len(span) <= 3000):
                self.blocks.append({"text": text, "span": span, "scope": e["scope"], "tag": e["tag"], "links": e["links"]})


def _identity(block, entity, domains):
    text, name = block["text"], entity["navn"]
    if not _phrase(text, name):
        return None
    mentions = gate.org_mentions(text)
    org = entity["organisasjonsnummer"]
    # A nearby different number makes attribution ambiguous even when our number also appears.
    if any(number != org for number, _, _ in mentions):
        return None
    name_pattern = r"(?<![a-z0-9])" + r"[\W_]+".join(re.escape(t) for t in gate.tokens(name)) + r"(?![a-z0-9])"
    labeled = re.compile(name_pattern + r"[\s,;:()\-]{0,12}(?:" + gate.ORG_LABEL.pattern + r")[\s.:()#-]{0,12}$", re.I)
    for number, start, _ in mentions:
        if number == org and labeled.search(gate.fold(text[:start])):
            return "legal_name_and_labeled_org_in_article"
    for link in block["links"]:
        if (_clean_url(link["url"]) and gate.registered_domain(link["url"]) in domains
                and _phrase(link["text"], name)):
            return "legal_name_and_company_domain_link_in_article"
    # Address corroboration is deliberately confined to an explicit address block.
    address = entity.get("forretningsadresse") or {}
    streets = address.get("adresse") or []
    streets = [streets] if isinstance(streets, str) else streets
    postcode, town = str(address.get("postnummer") or ""), str(address.get("poststed") or "")
    if (block["tag"] == "address" and postcode and town and _phrase(text, postcode + " " + town)
            and any(isinstance(street, str) and len(street) >= 4 and _phrase(text, street) for street in streets)):
        return "legal_name_and_registry_address_in_article"
    return None


def extract_article(html, page_url, entity, today, company_domains=()):
    """Return canonical value + exact spans + identity proof, or abstain. No I/O.

    Evidence auditors can call this with the selector's entity and company_domains.
    Both are public identity inputs; all publication evidence must be re-derived from html.
    """
    context = entity_context(entity)
    try:
        context["organisasjonsnummer"] = org_number(context["organisasjonsnummer"])
    except ValueError:
        return None
    domains = set(company_domains)
    url = _allowed_url(page_url, domains | declared_domains(context))
    if not url or not context["navn"] or not isinstance(html, str) or len(html) > 3_000_000:
        return None
    found = articles.article_published_date(html, today, url)
    if not found or not found.get("article_evidence") or not 8 <= len(found.get("title", "")) <= 250:
        return None
    parsed = parse_html(html, url)
    if parsed.get("canonical") and not articles._same_page(parsed["canonical"], url):
        return None
    # Do not let a neighboring article's structured data become this listing's date.
    nodes = [node for value in parsed.get("jsonld", []) for node in articles._article_nodes(value)]
    own = [node for node in nodes if articles._node_url(node) and articles._same_page(articles._node_url(node), url)]
    if nodes and any(articles._node_url(node) for node in nodes) and not own:
        return None
    parser = _Content(html, url)
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError):
        return None
    if parser.article_count > 1 or (parser.article_count == 0 and parser.main_count != 1):
        return None
    title = found["title"]
    core_name = " ".join(gate.name_tokens(context["navn"]))
    if not _phrase(title, core_name if len(core_name) >= 4 else context["navn"]):
        return None  # An incidental mention is not a publication about the target company.
    title_span = next((t["span"] for t in parser.titles if t["text"] == title or t["text"].startswith(title + " |")
                       or t["text"].startswith(title + " -")), None)
    if not title_span:
        return None
    scope = "article" if parser.article_count else "main"
    blocks = [b for b in parser.blocks if b["scope"] == scope]
    # Prefer a local paragraph. Never combine unrelated paragraphs to manufacture identity proof.
    for block in blocks:
        rule = _identity(block, context, domains)
        if rule:
            value = {"title": title, "url": url, "published_on": found["published_on"],
                     "published_at": found.get("published_at"), "publication_type": "third_party_news",
                     "source_kind": SOURCE_CLASS, "publisher_domain": gate.host(url)}
            proof = {"method": "independent_news_identity_v1", "organisation_number": context["organisasjonsnummer"],
                     "publishable": True, "rule": rule, "article_url": url, "evidence_span": block["span"]}
            return {"value": value, "spans": {"date": found["span"], "title": title_span, "identity": block["span"]},
                    "identity_proof": proof}
    return None


def _nomination_batches(fetcher, org, entity):
    key = search.api_key()
    if not key:
        return
    name = re.sub(r'["\r\n]', " ", str(entity.get("navn") or "")).strip()
    queries = [f'"{org}" (nyheter OR news OR pressemelding)', f'"{name}" (nyheter OR news OR pressemelding)']
    for query in queries[:MAX_QUERIES]:
        url = search.ENDPOINT + "?" + urlencode({"q": query, "country": "no", "count": 10, "safesearch": "strict"})
        response = fetcher.get(url, org, headers={"X-Subscription-Token": key, "Accept": "application/json"},
                               cost=search.cost_per_query(), store=False)
        body = response.json() if response.state == "available" else None
        web = body.get("web") if isinstance(body, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        results = results if isinstance(results, list) else []
        # Only the URL is retained. Search text and dates cannot be cited or published.
        yield [item["url"] for item in results[:10] if isinstance(item, dict) and isinstance(item.get("url"), str)], response.state


def research(profile, fetcher, entity, max_candidates=MAX_CANDIDATES, nominated_urls=()):
    """Fetch up to three independent articles, with at most two licensed search queries.

    Runs even without a verified company website. With no API key it is inactive unless
    the caller supplies public URLs (for example press links from a verified site).
    Connector failures never replace existing activity or website availability.
    """
    context = entity_context(entity)
    org = context["organisasjonsnummer"]
    limit = min(MAX_CANDIDATES, max(0, int(max_candidates)))
    with profile.lock:
        domains = company_domains(profile, context)
        seen = {_url_key(c["value"]["url"]) for c in profile.data["claims"] if c.get("field") == "company_publication"
                and isinstance(c.get("value"), dict) and isinstance(c["value"].get("url"), str)}
    attempt = {"strategy": METHOD, "search_enabled": search.enabled(), "queries": 0, "fetched": 0,
               "published": 0, "candidates": [], "state": "not_available"}
    profile.data["attempts"].append(attempt)
    if not limit or (not attempt["search_enabled"] and not nominated_urls):
        attempt["reason"] = "No licensed search key or caller-supplied public article candidates"
        return 0

    def consume(urls):
        for candidate in urls:
            if attempt["fetched"] >= limit:
                return
            url = _allowed_url(candidate, domains | declared_domains(context))
            if not url or _url_key(url) in seen:
                continue
            seen.add(_url_key(url))
            attempt["fetched"] += 1
            response = fetcher.get(url, org, source_class=SOURCE_CLASS)
            profile.snapshot(response)
            record = {"url": url, "state": response.state}
            attempt["candidates"].append(record)
            if response.state != "available":
                continue
            if "html" not in response.headers.get("content-type", "").lower():
                record.update(state="not_available", reason="Response is not HTML")
                continue
            final_key = _url_key(response.url)
            if final_key != _url_key(url) and final_key in seen:
                record.update(state="not_available", reason="Redirect duplicates an already checked article")
                continue
            seen.add(final_key)
            found = extract_article(response.text(), response.url, context, response.retrieved_at[:10], domains)
            if not found:
                record.update(state="ambiguous", reason="No exact company-bound article with its own title and publication date")
                continue
            value, proof = found["value"], found["identity_proof"]
            proof["registry_snapshot_id"] = profile.data["legal_identity"].get("source_snapshot_id")
            claim = None
            for part in ("identity", "title", "date"):
                span = found["spans"][part]
                claim = profile.add("company_publication", value, response, family="activity", key=value["url"],
                                    span=span, effective_at=value["published_on"], source_class=SOURCE_CLASS,
                                    scope="legal_entity", method=METHOD, identity_proof=proof,
                                    selector={"type": "independent_news_article", "part": part,
                                              "entity": context, "company_domains": sorted(domains)})
            if claim:
                record["state"] = "available"
                attempt["published"] += 1
                attempt["state"] = "available"

    consume(list(nominated_urls)[:MAX_NOMINATIONS])
    if attempt["search_enabled"] and attempt["fetched"] < limit:
        for urls, state in _nomination_batches(fetcher, org, context):
            attempt["queries"] += 1
            attempt["last_search_state"] = state
            consume(urls)
            if attempt["fetched"] >= limit or attempt["published"]:
                break
    return attempt["published"]
