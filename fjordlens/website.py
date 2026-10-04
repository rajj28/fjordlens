"""Budgeted company-site discovery, legal-entity verification and extraction."""
from __future__ import annotations
from datetime import datetime, timezone
import os
import html as stdlib_html
import re
from urllib.parse import urljoin, urlsplit, urlencode
from xml.etree import ElementTree as ET
from . import articles, discovery, gate, search
from .core import canonical
from .gate import registered_domain
from .html import clean, parse_html, structured_nodes
from .identity import assess, contains_name, host, jsonld_types, node_org, node_org_identifiers, normalize_name, page_org_numbers

SOCIAL_HOSTS = {"linkedin.com": "LinkedIn", "facebook.com": "Facebook", "instagram.com": "Instagram",
                "youtube.com": "YouTube", "youtu.be": "YouTube", "x.com": "X", "twitter.com": "X", "tiktok.com": "TikTok"}
EMAIL_PROVIDERS = {"gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no", "yahoo.com", "yahoo.no", "icloud.com", "me.com", "online.no", "frisurf.no", "broadpark.no", "lyse.net", "start.no", "msn.com", "proton.me", "protonmail.com"}
INTEREST = re.compile(r"kontakt|contact|om-oss|about|personvern|privacy|legal|impressum|career|karriere|ledig|stilling|jobs|nyhet|news|aktuelt|presse|press|location|kontor", re.I)

def valid_date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return None

def page_priority(link):
    value = link["url"] + " " + link.get("text", "")
    groups = (r"kontakt|contact|legal|impressum", None, r"nyhet|news|aktuelt|press|blogg|blog|artikler", r"om-oss|about|location|kontor")
    for i, pattern in enumerate(groups):
        if (CAREERS.search(value) if pattern is None else re.search(pattern, value, re.I)):
            return i
    return 9

def company_url(value):
    value = str(value or "").strip()
    if not value or "@" in value or " " in value:
        return None
    return value if value.startswith(("http://", "https://")) else "https://" + value

def add_web(profile, field, value, response, family, proof, *, span, key="", date=None, scope="company_reported", method="html_text_v1", selector=None):
    return profile.add(field, value, response, family=family, span=span[:3000], key=key,
                       effective_at=date, source_class="company_owned", method=method, scope=scope, identity_proof=proof, selector=selector)

def jsonld_quote(page, path, max_len=3000):
    """Verbatim slice of the raw JSON-LD script text for a parsed node path like "/0/@graph/2"."""
    import re as _re
    from . import jsonspan
    match = _re.match(r"^/(\d+)(/.*)?$", path or "")
    raws = page.get("jsonld_raw") or []
    if not match or int(match.group(1)) >= len(raws):
        return None
    text = raws[int(match.group(1))]
    try:
        span = jsonspan.index(text).get(match.group(2) or "")
    except ValueError:
        return None
    if not span:
        return None
    _, start, end = span
    return text[start:end] if end - start <= max_len else text[start:start + max_len]


PROFILE_PATHS = {
    "LinkedIn": re.compile(r"^/(company|school|showcase)/([^/?#]+)", re.I),
    "Facebook": re.compile(r"^/(?!sharer|share|dialog|plugins|tr|login|groups|events|photo|photos|watch|story\.php|permalink\.php|hashtag|search|help|policies|privacy|pages/category)(?:pages/[^/]+/)?([A-Za-z0-9.\-_]{2,})/?$", re.I),
    "Instagram": re.compile(r"^/(?!p/|reel/|reels/|explore/|accounts/|stories/|tv/)([A-Za-z0-9._]{2,})/?$", re.I),
    "YouTube": re.compile(r"^/(?:(?:channel|c|user)/([^/?#]+)|@([^/?#]+))/?$", re.I),
    "X": re.compile(r"^/(?!intent|share|home|search|hashtag|i/|login|signup)([A-Za-z0-9_]{2,15})/?$", re.I),
    "TikTok": re.compile(r"^/@([A-Za-z0-9._]{2,})/?$", re.I),
}


def social_profile_url(url):
    """(platform, handle, canonical_url) for a company profile link; None for share widgets, posts,
    personal profiles, search pages and other non-profile links. Query strings are dropped."""
    parts = urlsplit(url)
    h = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("no.")
    platform = {"linkedin.com": "LinkedIn", "facebook.com": "Facebook", "fb.com": "Facebook", "instagram.com": "Instagram",
                "youtube.com": "YouTube", "x.com": "X", "twitter.com": "X", "tiktok.com": "TikTok"}.get(h)
    if not platform:
        return None
    if platform == "Facebook" and parts.path.rstrip("/") == "/profile.php":
        match = re.search(r"(?:^|&)id=(\d+)", parts.query)
        return (platform, match.group(1), f"https://www.facebook.com/profile.php?id={match.group(1)}") if match else None
    match = PROFILE_PATHS[platform].match(parts.path or "/")
    if not match:
        return None
    handle = next(g for g in match.groups() if g) if platform != "LinkedIn" else match.group(2)
    canonical_host = {"X": "x.com"}.get(platform, h)
    return platform, handle, f"https://www.{canonical_host}{parts.path.rstrip('/')}"


def handle_matches_company(handle, legal_name, site_url):
    """A site-declared profile is kept only if its handle carries the company or domain name, which
    excludes the web agency's or a partner's profile linked from the same footer."""
    compact = re.sub(r"[^a-z0-9]", "", gate.fold(handle))
    words = [t for t in gate.name_tokens(legal_name) if t not in discovery.GENERIC]
    label = registered_domain(site_url).split(".")[0].replace("-", "")
    # Every distinctive word of the legal name, or the full domain label: a single shared word lets an agency
    # or partner account through ("bergenwebdesign" for BERGEN BYGG AS).
    return bool(compact) and ((bool(words) and all(t in compact for t in words)) or (len(label) >= 4 and label in compact))


def names_other_entity(page, legal_name):
    """True if the page's title/h1/og:title names a legal entity (word + legal-form suffix) that is
    not exactly ours, e.g. "Kontakt | Sister AS" or "Bergen Bil Eiendom AS" for BERGEN BIL AS."""
    ours = gate.name_tokens(legal_name)
    for text in (page.get("title", ""), page.get("h1", ""), page.get("meta", {}).get("og:title", "")):
        for match in gate.LEGAL_SUFFIX.finditer(text or ""):
            before = gate.tokens(text[max(0, match.start() - 160):match.start()])
            if before and before[-len(ours):] != ours:
                return True
    return False


# Careers words as whole words or path segments only: "utstillingsmodell" (exhibition model) is not a job page.
CAREERS = re.compile(r"(?<![a-zæøå])(karriere|career|careers|jobb|jobs|jobbe|ledige[-_ ]stillinger|ledig[-_ ]stilling|"
                     r"stillinger|stilling|vacancies|vacancy|join[-_ ]us|bli[-_ ]med)(?![a-zæøå])", re.I)


PLACEHOLDER_BRANDS = re.compile(r"^(hjem|home|forside|startside|velkommen|welcome|wordpress|wix|squarespace|webflow|shopify|"
                                r"just another wordpress site|min nettside|my site|untitled|index)$", re.I)


def public_brand(profile, org, page, response, proof, entity_name=""):
    """The name the verified site uses for itself (og:site_name, else its own Organization or WebSite
    markup). A public brand is labelled as such; it never replaces the registered legal name."""
    if any(c["field"] == "public_brand" for c in profile.data["claims"]):
        return
    domain = registered_domain(page["url"])
    candidates = []
    site_name = (page.get("meta") or {}).get("og:site_name") or ""
    if site_name:
        candidates.append((site_name, "html_og_site_name_v1", {"type": "parsed_html_pointer", "value": "/meta/og:site_name"}, site_name))
    for node, path in structured_nodes(page["jsonld"]):
        types = jsonld_types(node)
        if isinstance(node.get("name"), str) and (("WebSite" in types) or (types & {"Organization", "Corporation", "LocalBusiness"} and node_org(node) in {None, org})):
            candidates.append((node["name"], "jsonld_name_v1", {"type": "parsed_html_pointer", "value": "/jsonld" + path + "/name"},
                               jsonld_quote(page, path + "/name") or node["name"]))
    legal_words = {w for w in gate.name_tokens(entity_name) if w not in discovery.GENERIC and len(w) >= 3}
    label = domain.split(".")[0].replace("-", "")
    for raw, method, selector, span in candidates:
        brand = clean(re.sub(r"<[^>]+>", " ", raw))
        if not (2 <= len(brand) <= 80) or PLACEHOLDER_BRANDS.match(brand) or brand.lower().removeprefix("www.") in {domain, host(page["url"])}:
            continue
        brand_words = set(gate.tokens(brand))
        # A brand is reported only when it carries a distinctive word of the legal name or the domain label.
        if not (brand_words & legal_words or (len(label) >= 4 and label in "".join(brand_words))):
            continue
        add_web(profile, "public_brand", brand, response, "identity", proof, span=span, method=method, selector=selector, scope="company_reported")
        return


WORDPRESS_PLACEHOLDERS = {"hello world!", "hei verden!", "hallo verden!", "hello world", "hei verden"}
PAGINATION = re.compile(r"/(?:page|side|sida|p)/\d+/?$|[?&](?:page|side|paged|p)=\d+", re.I)
LISTING_PAGE = re.compile(r"nyhet|news|aktuelt|presse|press|blogg|blog|artikler|articles|arkiv|archive", re.I)
CONTACT_PAGE = re.compile(r"kontakt|contact", re.I)
PHONE_CUE = re.compile(r"(?:tlf|telefon|phone|mobil|mobile|ring oss|sentralbord)\.?\s*:?\s*((?:\+47|0047)?[\s.]?(?:\d[\s.]?){7}\d)(?!\d)", re.I)
ORG_TYPES = {"Organization", "Corporation", "LocalBusiness", "ProfessionalService", "Store", "Restaurant", "AutoDealer",
             "HomeAndConstructionBusiness", "GeneralContractor", "Electrician", "Plumber", "LegalService", "AccountingService"}


MISSING_PATHS = (("activity", ("/nyheter", "/aktuelt", "/blogg", "/news")), ("contact", ("/kontakt", "/kontakt-oss", "/contact")))


def probe_missing(profile, fetcher, org, entity, home, proof, visited):
    """After the link-driven crawl, a family still without facts gets up to two conventional pages on the verified
    site (robots-checked like every page; the same page-scope gates apply)."""
    for family, paths in MISSING_PATHS:
        if profile.data["availability"][family]["state"] == "available":
            continue
        tried = 0
        for path in paths:
            url = urljoin(home.url, path)
            if url in visited or url + "/" in visited or tried >= 2:
                continue
            tried += 1
            visited.add(url)
            r = fetcher.get(url, org)
            profile.snapshot(r)
            profile.data["attempts"].append({"strategy": "missing_type_probe_v1", "family": family, "url": url, "state": r.state})
            if r.state == "available" and "html" in r.headers.get("content-type", "text/html") and host(r.url) == host(home.url) \
                    and r.url.rstrip("/") != home.url.rstrip("/"):
                visited.add(r.url)
                extract(profile, entity, parse_html(r.text(), r.url), r, proof)
                if profile.data["availability"][family]["state"] == "available":
                    break


def known_claim(profile, field, key):
    """First source wins: the same fact found again by another extractor is not a second, conflicting claim."""
    with profile.lock:
        return any(c["field"] == field and c.get("key") == key for c in profile.data["claims"])


def known_publication(profile, url):
    return known_claim(profile, "company_publication", url)


def known_phone(profile, digits):
    with profile.lock:
        return any(c["field"] == "website_phone" and re.sub(r"\D", "", str(c["value"]))[-8:] == digits for c in profile.data["claims"])


def article_page(profile, page, response, proof):
    """A single article on the verified site that states its own publication date (meta, JSON-LD or time element)."""
    parts = urlsplit(response.url)
    segments = [x for x in parts.path.split("/") if x]
    if len(segments) < 2 or not LISTING_PAGE.search("/".join(segments[:-1])) or host(response.url) != host(proof["url"]):
        return
    if PAGINATION.search(parts.path + ("?" + parts.query if parts.query else "")) or segments[-1].isdigit():
        return
    url = f"{parts.scheme}://{parts.netloc}{parts.path}"
    found = articles.article_published_date(response.text(), response.retrieved_at[:10], page_url=response.url)
    if not found or len(found.get("title") or "") < 8 or known_publication(profile, url):
        return
    value = {"title": found["title"][:300], "url": url, "published_on": found["published_on"], "source_kind": "company_owned",
             "independent_sentiment": False}
    add_web(profile, "company_publication", value, response, "activity", proof, span=found["span"], key=url,
            date=found["published_on"], scope="company_published_content", method="html_article_date_v1",
            selector={"type": "article_date", "source": found["source"], "value": found["span"][:500]})


def dated_links(profile, page, response, proof):
    """Company publications from a news/blog listing page (or the homepage): an article link on the verified site
    with a publication date shown next to it (time element, written date, or /YYYY/MM/DD/ in the URL)."""
    path = urlsplit(page["url"]).path
    if not (LISTING_PAGE.search(path) or path.strip("/") == "") or host(page["url"]) != host(proof["url"]):
        return
    for item in articles.extract_dated_links(response.text(), response.url, response.retrieved_at[:10]):
        if known_publication(profile, item["url"]):
            continue
        value = {"title": item["title"], "url": item["url"], "published_on": item["published_on"],
                 "source_kind": "company_owned", "independent_sentiment": False}
        add_web(profile, "company_publication", value, response, "activity", proof, span=item["span"], key=item["url"],
                date=item["published_on"], scope="company_published_content", method="html_dated_link_v1",
                selector={"type": "dated_link", "url": item["url"], "title": item["title"], "date_source": item["date_source"]})


THIRD_PARTY = re.compile(r"regnskap|revisor|revisjon|advokat|partner|samarbeid|leverand|levert av|utviklet av|webdesign|design(?:et)? av|"
                         r"utleier|huseier|gårdeier|forhandler|agent|megler|bank|forsikring|legevakt|politi|nødnummer|kommune|fylke|"
                         r"accountant|auditor|lawyer|supplier|landlord|developed by|powered by|designed by", re.I)
OTHER_ENTITY = re.compile(r"\b[A-ZÆØÅ][\wÆØÅæøå&.\-]*(?:\s+[\wÆØÅæøå&.\-]+){0,5}\s+(?:AS|ASA|ANS|DA|SA|NUF|BA|KS)\b")
NUMBER_SEPARATOR = r"(?:\s|&nbsp;|&#160;|&#xa0;|[.\-]|<[^<>]{0,60}>)*"


def verbatim_number(text, digits):
    """The phone number exactly as written in the source (spaces, entities or inline tags between digits)."""
    pattern = r"(?<!\d)(?:(?:\+|00)47" + NUMBER_SEPARATOR + ")?" + NUMBER_SEPARATOR.join(digits) + r"(?!\d)"
    match = re.search(pattern, text, re.I)
    return match.group(0) if match else None


def contact_phones(profile, page, response, proof, legal_name=""):
    """Phone numbers the verified site's own contact page labels as such (cue word next to the number)."""
    seen = set()
    own = core_name(legal_name)
    lines = page.get("lines", [])[:400]
    text = response.text()
    for index, line in enumerate(lines):
        context = " ".join(lines[max(0, index - 2):index + 1])
        if THIRD_PARTY.search(context) or any(not own or not core_name(m.group(0)).endswith(own) for m in OTHER_ENTITY.finditer(context)):
            continue  # an accountant's, landlord's, partner's or other company's number is not ours
        for match in PHONE_CUE.finditer(line):
            written = match.group(1).strip()
            digits = re.sub(r"\D", "", written)
            digits = digits[2:] if digits.startswith("47") and len(digits) == 10 else digits[4:] if digits.startswith("0047") else digits
            if len(digits) != 8 or digits in seen or digits[0] not in "2345679" or known_phone(profile, digits):
                continue
            raw = verbatim_number(text, digits)
            if not raw:
                continue
            seen.add(digits)
            add_web(profile, "website_phone", written, response, "contact", proof, span=raw,
                    key=digits, method="html_contact_page_v1", selector={"type": "normalized_text", "value": match.group(0)})
            if len(seen) >= 3:
                return


LEGAL_FORMS = {"as", "asa", "ans", "da", "sa", "enk", "nuf", "ba", "ks", "sf", "iks"}


def core_name(value):
    """A legal name without its legal-form word(s): "Bergen Bil AS" -> "bergen bil"."""
    words = normalize_name(value).split()
    while words and words[-1] in LEGAL_FORMS:
        words.pop()
    while words and words[0] in LEGAL_FORMS:
        words.pop(0)
    return " ".join(words)


def own_organisation(node, org, legal_name):
    """The site's own organisation node: our organisation number, or exactly our legal name (with or without the
    legal form) and no conflicting identifier. A longer name ("BERGEN BIL EIENDOM AS") is another legal entity."""
    if not jsonld_types(node) & ORG_TYPES:
        return False
    numbers = {item.get("organisation_number") for item in node_org_identifiers(node)}
    if numbers:
        return numbers == {org}
    target = core_name(legal_name)
    return bool(target) and any(core_name(node.get(key)) == target for key in ("legalName", "name") if isinstance(node.get(key), str))


def same_as(profile, page, response, proof, node, path, legal_name):
    """Social profiles the site's own organisation markup declares (schema.org sameAs)."""
    links = node.get("sameAs")
    links = [links] if isinstance(links, str) else links if isinstance(links, list) else []
    quote = jsonld_quote(page, (path or "") + "/sameAs") or canonical(links)[:3000]
    for index, link in enumerate(links[:12]):
        if not isinstance(link, str):
            continue
        profile_url = social_profile_url(link)
        if not profile_url or not handle_matches_company(profile_url[1], legal_name, proof["url"]):
            continue
        platform, url = profile_url[0], profile_url[2]
        if known_claim(profile, "company_linked_profile", url):
            continue
        value = {"platform": platform, "url": url, "url_as_published": link, "relationship": "declared_in_site_organization_markup",
                 "destination_verified": False}
        add_web(profile, "company_linked_profile", value, response, "social_profiles", proof, span=quote, key=url,
                scope="site_declared_link", method="jsonld_sameas_v1",
                selector={"type": "parsed_html_pointer", "value": "/jsonld" + (path or "") + f"/sameAs/{index}"})


def wordpress_posts(profile, fetcher, org, home, proof):
    """Dated posts from the verified site's own WordPress REST API (one request, JSON evidence)."""
    if "wp-json" not in home.text()[:400000]:
        return
    url = urljoin(home.url, "/wp-json/wp/v2/posts?per_page=10&_fields=id,date,link,title")
    r = fetcher.get(url, org)
    profile.snapshot(r)
    profile.data["attempts"].append({"strategy": "wordpress_posts_v1", "url": url, "state": r.state})
    if r.state != "available" or host(r.url) != host(home.url):
        return
    try:
        posts = r.json()
    except ValueError:
        return
    if not isinstance(posts, list):
        return
    for index, post in enumerate(posts[:10]):
        if not isinstance(post, dict):
            continue
        link, published = post.get("link"), valid_date(post.get("date"))
        title = clean(re.sub(r"<[^>]+>", " ", str((post.get("title") or {}).get("rendered") or "") if isinstance(post.get("title"), dict) else ""))
        title = stdlib_html.unescape(title)
        if not isinstance(link, str) or host(link) != host(home.url) or not published or not title:
            continue
        if title.strip().lower() in WORDPRESS_PLACEHOLDERS:
            continue  # WordPress's default first post is not company activity.
        if published > r.retrieved_at[:10]:
            continue
        if known_publication(profile, link):
            continue
        value = {"title": title[:300], "url": link, "published_on": published, "source_kind": "company_owned",
                 "independent_sentiment": False}
        # JSON evidence: the quote is the verbatim slice of this post's object in the API response.
        profile.add("company_publication", value, r, pointer=f"/{index}", family="activity", key=link, effective_at=published,
                    source_class="company_owned", method="json_pointer_v1", scope="company_published_content", identity_proof=proof)


def extract(profile, entity, page, response, proof):
    org = entity["organisasjonsnummer"]
    if host(response.url) != host(proof["url"]):
        return
    if page_org_numbers(page) - {org}:
        profile.data["attempts"].append({"strategy": "page_scope_gate_v1", "url": response.url, "state": "ambiguous", "reason": "Additional legal entity on subpage"})
        return
    if names_other_entity(page, entity.get("navn") or ""):
        # A page titled after another legal entity (a sister company, a client, a partner) is
        # about that entity even on our verified site: none of its facts are attributed to us.
        profile.data["attempts"].append({"strategy": "page_scope_gate_v2", "url": response.url, "state": "ambiguous",
                                         "reason": "Page title or heading names a different legal entity"})
        return
    for index, contact in enumerate(page["contacts"]):
        kind, value = contact.split(":", 1)
        value = value.split("?", 1)[0].strip()
        if kind.lower() == "mailto":
            if not value.lower().endswith("@" + host(proof["url"])):
                continue
        else:
            original = re.sub(r"\D", "", str(entity.get("telefon", "")))[-8:]
            if len(original) != 8 or re.sub(r"\D", "", value)[-8:] != original:
                continue
        add_web(profile, "website_email" if kind.lower() == "mailto" else "website_phone", value,
                response, "contact", proof, span=contact, key=value, method="html_contact_v1",
                selector={"type": "parsed_html_pointer", "value": f"/contacts/{index}"})
    description = clean(re.sub(r"<[^>]+>", " ", page.get("description") or ""))
    is_about_page = urlsplit(page["url"]).path.strip("/") == "" or re.search(r"om-oss|about|selskapet|hvem-er-vi|bedriften|firma", page["url"], re.I)
    if description and len(description) >= 35 and "<" not in (page.get("description") or "")[:200] and is_about_page and host(page["url"]) == host(proof["url"]):
        if not any(c["field"] == "business_description" for c in profile.data["claims"]):
            add_web(profile, "business_description", description[:1500], response, "description", proof,
                    span=page["description"], method="html_meta_description_v1", selector={"type": "parsed_html_pointer", "value": "/description"})
    if is_about_page and host(page["url"]) == host(proof["url"]) and not any(c["field"] == "business_description" for c in profile.data["claims"]):
        # No meta description: the first substantial paragraph of the homepage/about page, verbatim.
        for index, line in enumerate(page.get("lines", [])[:80]):
            if 80 <= len(line) <= 900 and not re.search(r"cookie|informasjonskapsl|samtykke|consent|javascript|personvern|privacy|©|copyright", line, re.I) \
                    and len(re.findall(r"[A-Za-zÆØÅæøå]{3,}", line)) >= 12:
                add_web(profile, "business_description", line[:600], response, "description", proof, span=line[:600],
                        method="html_first_paragraph_v1", selector={"type": "parsed_html_pointer", "value": f"/lines/{index}"})
                break
    if urlsplit(page["url"]).path.strip("/") == "" and host(page["url"]) == host(proof["url"]):
        public_brand(profile, org, page, response, proof, entity.get("navn") or "")
    try:
        dated_links(profile, page, response, proof)
        article_page(profile, page, response, proof)
    except Exception as exc:  # noqa: BLE001 - malformed markup must not stop the remaining extraction
        profile.data["attempts"].append({"strategy": "publication_extraction_v1", "url": response.url, "state": "failed",
                                         "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
    if CONTACT_PAGE.search(urlsplit(page["url"]).path):
        contact_phones(profile, page, response, proof, entity.get("navn") or "")
    for index, link in enumerate(page["links"]):
        profile_url = social_profile_url(link["url"])
        if (profile_url and handle_matches_company(profile_url[1], entity.get("navn") or "", proof["url"])
                and not known_claim(profile, "company_linked_profile", profile_url[2])):
            platform, url = profile_url[0], profile_url[2]
            value = {"platform": platform, "url": url, "relationship": "linked_by_verified_company_site", "destination_verified": False}
            add_web(profile, "company_linked_profile", value, response, "social_profiles", proof,
                    span=link.get("href") or link["url"], key=url, scope="site_declared_link", method="html_href_v1", selector={"type": "parsed_html_pointer", "value": f"/links/{index}/href"})
        if (CAREERS.search(urlsplit(link["url"]).path + " " + link["text"]) and host(link["url"]) == host(proof["url"])
                and not known_claim(profile, "careers_page_url", link["url"])):
            # A careers page is not a hiring fact (only a job-feed item or a posting is): it is part of the website.
            add_web(profile, "careers_page_url", {"url": link["url"], "title": link["text"] or "Careers"}, response,
                    "website", proof, span=link.get("href") or link["url"], key=link["url"], method="html_href_v1", selector={"type": "parsed_html_pointer", "value": f"/links/{index}/href"})
    for node, path in structured_nodes(page["jsonld"]):
        types = jsonld_types(node)
        if "JobPosting" in types:
            employer = node.get("hiringOrganization") or {}
            if not isinstance(employer, dict):
                continue
            identifiers = node_org_identifiers(employer)
            exact_employer = (all(item["organisation_number"] == org for item in identifiers) if identifiers
                              else normalize_name(employer.get("legalName") or employer.get("name")) == normalize_name(entity["navn"]))
            if not exact_employer:
                continue
            posted = valid_date(node.get("datePosted"))
            if not node.get("title") or not posted:
                continue
            expires = valid_date(node.get("validThrough"))
            if expires and expires < response.retrieved_at[:10]:
                continue  # Expired on the company's own page: no longer a hiring fact.
            target = node.get("url") or page["url"]
            if not isinstance(target, str):
                continue
            identifier = node.get("identifier")
            identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
            job_key = str(identifier or target)
            value = {"title": clean(node["title"]), "url": urljoin(page["url"], target), "date_posted": posted,
                     "valid_through": valid_date(node.get("validThrough")), "employment_type": node.get("employmentType"),
                     "location": node.get("jobLocation"), "employer": employer.get("name"), "status": "source_reported"}
            add_web(profile, "job_posting", value, response, "hiring", proof, span=jsonld_quote(page, path) or canonical(node)[:3000], key=job_key, date=posted, method="jsonld_jobposting_v1", selector={"type": "parsed_html_pointer", "value": "/jsonld" + path})
        if any(t in {"NewsArticle", "Article", "BlogPosting", "PressRelease"} for t in types):
            headline = node.get("headline") or node.get("name")
            date = valid_date(node.get("datePublished"))
            target = node.get("url") or page["url"]
            if not isinstance(target, str) or not headline or not date:
                continue
            target = urljoin(page["url"], target)
            if host(target) != host(proof["url"]):
                continue
            if date > response.retrieved_at[:10] or known_publication(profile, target):
                continue
            add_web(profile, "company_publication", {"title": clean(headline), "url": target, "published_on": date,
                                                      "source_kind": "company_owned", "independent_sentiment": False}, response,
                    "activity", proof, span=jsonld_quote(page, path) or canonical(node)[:3000], key=target, date=date, scope="company_published_content", method="jsonld_article_v1", selector={"type": "parsed_html_pointer", "value": "/jsonld" + path})
        if gate.OWNER_NODE_PATH.match(path or "/0") and own_organisation(node, org, entity.get("navn") or ""):
            same_as(profile, page, response, proof, node, path, entity.get("navn") or "")
        if node_org(node) == org:
            for name, field in (("telephone", "website_phone"), ("email", "website_email"), ("address", "website_contact_address")):
                if node.get(name):
                    add_web(profile, field, node[name], response, "contact", proof, span=jsonld_quote(page, path + "/" + name) or canonical(node[name]), method="jsonld_organization_v1", selector={"type": "parsed_html_pointer", "value": "/jsonld" + path + "/" + name})

def feed(profile, entity, response, proof):
    if host(response.url) != host(proof["url"]):
        return
    try:
        if b"<!DOCTYPE" in response.body.upper() or b"<!ENTITY" in response.body.upper():
            return
        root = ET.fromstring(response.body)
    except ET.ParseError:
        return
    raw_text = response.text()
    raw_items = [m.group(0) for m in re.finditer(r"(?s)<(?:[A-Za-z0-9_]+:)?(item|entry)\b.*?</(?:[A-Za-z0-9_]+:)?\1>", raw_text)]
    count, position = 0, -1
    for item in list(root.iter()):
        if item.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        position += 1
        values = {child.tag.split("}")[-1]: (child.text or "").strip() for child in item}
        title = values.get("title")
        target = values.get("link")
        if not target:
            target = next((c.attrib.get("href") for c in item if c.tag.split("}")[-1] == "link"), None)
        date = values.get("pubDate") or values.get("published") or values.get("updated")
        parsed = valid_date(date)
        if not parsed and date:
            try:
                from email.utils import parsedate_to_datetime
                parsed = parsedate_to_datetime(date).date().isoformat()
            except (ValueError, TypeError):
                pass
        if title and target and parsed and parsed <= response.retrieved_at[:10] and host(target) == host(proof["url"]):
            add_web(profile, "company_publication", {"title": clean(title), "url": target, "published_on": parsed,
                                                      "source_kind": "company_owned", "independent_sentiment": False}, response,
                    "activity", proof, span=(raw_items[position] if position < len(raw_items) else ET.tostring(item, encoding="unicode"))[:3000], key=target, date=parsed,
                    scope="company_published_content", method="rss_atom_item_v1")
            count += 1
        if count >= 20:
            break

VERIFY = re.compile(r"kontakt|contact|om-oss|om oss|about|personvern|privacy|impressum|legal|vilk[aå]r|terms|firma|selskap", re.I)
RETRY_HTTP = re.compile(r"CERTIFICATE|certificate|SSL|TLS|handshake|timed out|refused|reset|unreachable|EOF occurred|Remote end closed", re.I)
SITE_FAMILIES = ("description", "contact", "social_profiles", "hiring", "activity")


def fetch_page(profile, fetcher, org, url):
    """GET with bounded fallbacks for small-site setups: the other www/non-www host when DNS
    fails, then plain HTTP when no HTTP response arrived at all (TLS, refused, timeout, or an
    unreachable robots.txt). An HTTP error status is an answer and is not retried here."""
    r = fetcher.get(url, org)
    profile.snapshot(r)
    if r.state == "available" or r.status or (r.error or "").startswith(("Robots:", "Budget:", "Cutoff:")):
        return r
    parts = urlsplit(url)
    alternates = []
    if r.error and re.search(r"getaddrinfo|Name or service|nodename|DNS|Hostname mismatch|certificate is not valid for", r.error):
        other = parts.hostname[4:] if parts.hostname.startswith("www.") else "www." + parts.hostname
        alternates.append(parts._replace(netloc=other).geturl())
    if url.startswith("https://"):
        alternates.append("http://" + url[len("https://"):])
    for alt_url in alternates[:2]:
        alt = fetcher.get(alt_url, org)
        profile.snapshot(alt)
        if alt.state == "available":
            return alt
    return r


def html_page(response):
    if response.state != "available" or "html" not in response.headers.get("content-type", "text/html").lower():
        return None
    return parse_html(response.text(), response.url)


def verification_links(page, base_url, limit=2):
    """Same-site contact/about/legal pages, contact first, no duplicates or documents."""
    domain = registered_domain(base_url)
    picked, seen = [], {urlsplit(base_url).path.rstrip("/")}
    def rank(link):
        text = link["url"] + " " + link.get("text", "")
        return next((i for i, p in enumerate((r"kontakt|contact", r"om-oss|om oss|about|firma|selskap", r"personvern|privacy|impressum|legal|vilk")) if re.search(p, text, re.I)), 9)
    for link in sorted(page["links"], key=lambda l: (rank(l), len(l["url"]))):
        parts = urlsplit(link["url"])
        path = parts.path.rstrip("/")
        if registered_domain(link["url"]) != domain or path in seen or not VERIFY.search(link["url"] + " " + link.get("text", "")):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?)$", path, re.I):
            continue
        seen.add(path)
        picked.append(link)
        if len(picked) >= limit:
            break
    return picked


def publish_declared_profiles(profile, declared):
    """A registry 'website' that is a social profile is still a company-declared profile."""
    response = getattr(profile, "identity_response", None)
    if response is None:
        return
    for item in declared:
        if item.get("origin") != "registry_website":
            continue
        value = {"platform": item["platform"], "url": item["url"], "relationship": "declared_as_website_in_official_registry",
                 "destination_verified": False}
        profile.add("company_profile", value, response, pointer="/hjemmeside", family="social_profiles",
                    key=item["url"], source_class="official_registry", scope="registry_declared_link")


def try_candidates(profile, fetcher, org, ctx, candidates, last_state):
    """Fetch each candidate's homepage (plus verification pages) and return the first gate-accepted site."""
    for cand in candidates:
        r = fetch_page(profile, fetcher, org, cand["url"])
        page = html_page(r)
        if page is None:
            profile.data["attempts"].append({"strategy": "candidate_fetch_v2", "origin": cand["origin"], "url": cand["url"],
                                             "state": r.state, "error": (r.error or f"HTTP {r.status}")[:200]})
            if r.state in {"blocked", "failed"} and last_state == "not_available":
                last_state = r.state
            continue
        pages, responses = [page], [r]
        verdict = gate.assess(ctx, cand, pages)
        if not verdict["hard_reject"] and not verdict["signals"].get("org_on_homepage_owner_position"):
            for link in verification_links(page, r.url):
                sub = fetch_page(profile, fetcher, org, link["url"])
                subpage = html_page(sub)
                if subpage and registered_domain(sub.url) == registered_domain(r.url):
                    pages.append(subpage)
                    responses.append(sub)
            if len(pages) > 1:
                verdict = gate.assess(ctx, cand, pages)
        profile.data["identity_assessments"].append(verdict)
        if verdict["publishable"]:
            return (verdict, pages, responses), last_state
        last_state = "ambiguous"
    return None, last_state


def research(profile, fetcher, entity, max_pages=7, caches=None):
    org = entity["organisasjonsnummer"]
    with profile.lock:
        units = [c["value"] for c in profile.data["claims"] if c["field"] == "registered_workplace" and isinstance(c["value"], dict)]
    # Workplace addresses, phones and e-mails are registry contacts too (gate evidence E4).
    workplaces = [{**(u["address"] if isinstance(u.get("address"), dict) else {}), "telefon": u.get("phone"), "mobil": u.get("mobile"),
                   "epostadresse": u.get("email")} for u in units]
    with profile.lock:
        people = [c["value"].get("name") for c in profile.data["claims"] if c["field"] == "registered_role" and isinstance(c["value"], dict)
                  and c["value"].get("role_code") in {"DAGL", "LEDE", "INNH", "DTPR", "DTSO"} and isinstance(c["value"].get("name"), str)]
    ctx = gate.Context(entity, workplaces, people)
    brands = sorted({u.get("name") for u in units if isinstance(u.get("name"), str)})[:4]
    sites, declared = discovery.candidates(entity, caches=caches if caches is not None else getattr(fetcher, "caches", None),
                                           dns=bool(getattr(fetcher, "dns_enabled", False)), brands=brands, workplaces=units)
    publish_declared_profiles(profile, declared)
    if not sites and not search.enabled():
        profile.state("website", "not_available", "No registry website, business e-mail domain or name-derived domain candidate exists")
        for family in SITE_FAMILIES:
            if profile.data["availability"][family]["state"] != "available":
                profile.state(family, "not_available", "No company website candidate to research")
        return
    accepted, last_state = try_candidates(profile, fetcher, org, ctx, sites[:5], "not_available")
    if not accepted and search.enabled():
        # Optional licensed search (only with an evaluator-supplied key): nominations face the same gate.
        nominated = search.nominate(fetcher, entity, exclude={registered_domain(c["url"]) for c in sites})
        profile.data["attempts"].append({"strategy": "search_nomination_v1", "state": "available" if nominated else "not_available",
                                         "candidates": [c["url"] for c in nominated]})
        if nominated:
            accepted, last_state = try_candidates(profile, fetcher, org, ctx, nominated, last_state)
        elif not sites:
            profile.state("website", "not_available", "No registry website, business e-mail domain, name-derived domain or search candidate exists")
            for family in SITE_FAMILIES:
                if profile.data["availability"][family]["state"] != "available":
                    profile.state(family, "not_available", "No company website candidate to research")
            return
    if not accepted:
        reason = "No candidate passed the exact-company identity gate" if last_state == "ambiguous" else "Candidate sites could not be fetched"
        profile.state("website", last_state, reason)
        for family in SITE_FAMILIES:
            if profile.data["availability"][family]["state"] != "available":
                profile.state(family, last_state, "Site-derived claims withheld because no exact company site was verified")
        return
    verdict, pages, responses = accepted
    home, proof_response = responses[0], responses[verdict["proof_page_index"]]
    proof = {**verdict, "url": home.url, "snapshot_id": proof_response.snapshot_id,
             "registry_snapshot_id": profile.data["legal_identity"].get("source_snapshot_id")}
    declared_path = urlsplit(verdict["candidate_url"]).path
    final = urlsplit(home.url)
    # A root candidate that redirects to a landing page (e.g. "/take-away/") is still the site root.
    site_url = f"{final.scheme}://{final.netloc}/" if declared_path in ("", "/") else home.url
    add_web(profile, "official_website", site_url, proof_response, "website", proof, span=verdict["proof_span"],
            scope="legal_entity", method="identity_gate_v2", selector=verdict["proof_selector"])
    section = [part for part in declared_path.lower().split("/") if part and part not in
               {"no", "nb", "nn", "en", "se", "home", "hjem", "forside", "index.html", "index.php", "default.aspx"}]
    if section:
        # The company's page is one section of a larger (group) site: site-wide facts belong to
        # the larger site, so nothing beyond the website itself is attributed to this company.
        for family in SITE_FAMILIES:
            if profile.data["availability"][family]["state"] != "available":
                profile.state(family, "not_available", "Verified page is a section of a larger site; site-wide facts are not attributed to this company")
        return
    visited = {resp.url for resp in responses}
    queue, feeds = [], []
    for page, resp in zip(pages, responses):
        extract(profile, entity, page, resp, proof)
        queue.extend(l for l in page["links"] if registered_domain(l["url"]) == registered_domain(home.url) and INTEREST.search(l["url"] + " " + l["text"]))
        feeds.extend(page["feeds"])
    if len(queue) < 3:
        sm = fetcher.get(urljoin(home.url, "/sitemap.xml"), org)
        profile.snapshot(sm)
        if sm.state == "available" and b"<!DOCTYPE" not in sm.body.upper() and b"<!ENTITY" not in sm.body.upper():
            try:
                root = ET.fromstring(sm.body)
                for elem in root.iter():
                    if elem.tag.split("}")[-1] == "loc" and elem.text and registered_domain(elem.text) == registered_domain(home.url) and INTEREST.search(elem.text):
                        queue.append({"url": elem.text.strip(), "text": ""})
            except ET.ParseError:
                pass
    unique = {l["url"]: l for l in queue if l["url"] not in visited and not re.search(r"\.(pdf|zip|jpg|png|mp4)(\?|$)", l["url"], re.I)}
    queue = sorted(unique.values(), key=lambda l: (page_priority(l), len(l["url"]), l["url"]))
    ordered, groups = [], {}
    for link in queue:  # Round-robin intent coverage prevents contact pages consuming all depth.
        groups.setdefault(page_priority(link), []).append(link)
    while any(groups.values()):
        for priority in sorted(groups):
            if groups[priority]:
                ordered.append(groups[priority].pop(0))
    for link in ordered[:max(0, max_pages - len(pages))]:
        r = fetcher.get(link["url"], org)
        visited.update({link["url"], r.url})
        profile.snapshot(r)
        profile.data["attempts"].append({"strategy": "targeted_static_v1", "url": r.url, "state": r.state, "snapshot_id": r.snapshot_id})
        if r.state == "available" and "html" in r.headers.get("content-type", "text/html") and registered_domain(r.url) == registered_domain(home.url):
            page = parse_html(r.text(), r.url)
            extract(profile, entity, page, r, proof)
            feeds.extend(page["feeds"])
    probe_missing(profile, fetcher, org, entity, home, proof, visited)
    wordpress_posts(profile, fetcher, org, home, proof)
    for url in list(dict.fromkeys(feeds))[:1]:
        if registered_domain(url) != registered_domain(home.url):
            continue
        r = fetcher.get(url, org)
        profile.snapshot(r)
        if r.state == "available":
            feed(profile, entity, r, proof)
    for family in SITE_FAMILIES:
        if profile.data["availability"][family]["state"] != "available":
            profile.state(family, "not_available", "No supported exact-company claim found in the bounded site crawl; this does not establish absence", complete=False)
