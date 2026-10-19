"""Dated company articles from HTML (news/blog listing pages and single article pages).

Every returned `span` is a substring copied verbatim from the HTML source, so evidence quotes stay literal.
Links count only on the page's own host (ignoring "www."), and a date is paired with a link only when nothing else
on the page could own it: inside the link, or in the nearest enclosing element that links to no other page.
"""
from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, quote, unquote
from .html import static_page_template

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
FILE_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx", ".xls", ".xlsx")
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "PressRelease"}
MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "desember": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11, "des": 12,
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "october": 10, "december": 12,
    "oct": 10, "dec": 12,
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_PATTERNS = (
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)"), "dmy"),
    (re.compile(rf"(?<!\d)(\d{{1,2}})\.?\s+({_MONTH})\.?\s+(\d{{4}})(?!\d)", re.I), "d_name_y"),
    (re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}}),\s*(\d{{4}})(?!\d)", re.I), "name_d_y"),
)
URL_DATE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")
MAX_QUOTE = 2000          # a link+date quote longer than this is not captured: the item is skipped
MAX_DEPTH = 120           # enclosing elements tracked per position (deeper markup is transparent)
MAX_ELEMENTS = 40000      # elements tracked per page
TEXT_CAP = 3000           # characters of text kept per element (bigger elements are lists, not items)
ACTION_LABEL = re.compile(r"^(?:les mer|les hele (?:saken|artikkelen)|read more|read (?:the )?(?:article|story)|continue reading|se mer)(?:\b|[.›»])", re.I)
CONVERSION_LABEL = re.compile(
    r"^(?:book(?:e)?\s+(?:(?:a|an|en|et|din|ditt|gratis|free)\s+){0,2}(?:prat|chat|call|meeting|demo|m\u00f8te|mote|time|appointment|consultation)"
    r"|(?:bestill|reserver|schedule)\s+(?:(?:a|an|en|et|din|ditt|gratis|free)\s+){0,2}(?:prat|chat|call|meeting|demo|m\u00f8te|mote|time|appointment|consultation)"
    r"|kontakt\s+oss|contact\s+us|ta\s+kontakt|get\s+in\s+touch"
    r"|send\s+(?:oss\s+en\s+melding|(?:us\s+)?a\s+message)"
    r"|(?:get|request)\s+(?:a\s+)?quote|f\u00e5\s+(?:et\s+)?tilbud)(?:\b|\s|$)", re.I)
NONPUBLICATION_ROOTS = {
    "kontakt", "kontakt-oss", "kontaktoss", "contact", "contact-us", "contactus", "contacts",
    "personvern", "personvernerklaering", "personvernerkl\u00e6ring", "privacy", "privacy-policy", "privacy-statement",
    "datenschutz", "cookies", "cookie-policy", "cookie-policy-statement", "informasjonskapsler",
    "legal", "legal-notice", "imprint", "impressum", "terms", "terms-and-conditions", "terms-of-use",
    "vilkar", "vilk\u00e5r", "betingelser", "salgsbetingelser", "brukervilkar", "brukervilk\u00e5r",
    "tjenester", "tjeneste", "services", "service", "produkter", "products", "shop", "cart", "checkout",
    "om", "om-oss", "omoss", "about", "about-us", "aboutus", "company", "our-company",
    "booking", "book-demo", "book-a-demo", "book-a-call", "bestilling", "login", "logg-inn", "sitemap",
}
DATE_LABEL = re.compile(r"dateModified|datePublished|\b(?:updated|modified|oppdatert|endret|published|posted|publisert|offentliggjort)\b", re.I)
MODIFICATION_LABELS = {"datemodified", "updated", "modified", "oppdatert", "endret"}
ARTICLE_NOISE = re.compile(r"(?:^|[\s_-])(?:footer|copyright|comment\w*|review\w*|related|recommend\w*|sidebar|share)(?:$|[\s_-])", re.I)
PUBLICATION_PREFIX = re.compile(r"(?:^|[\s:|])(?:published|publisert|offentliggjort)(?:\s+(?:on|at|den))?\s*[:\-]?\s*$", re.I)
PUBLICATION_TEXT = re.compile(rf"^\s*(?:published|publisert|offentliggjort)(?:\s+(?:on|at|den))?\s*[:\-]?\s*(?:\d|(?:{_MONTH})\b)", re.I)
ARTICLE_BLOCKS = {"article", "main", "section", "div", "p", "li", "header", "footer", "aside", "nav", "blockquote", "h1", "h2", "h3"}


def modification_date(text):
    labels = DATE_LABEL.findall(text)
    return bool(labels and labels[-1].lower() in MODIFICATION_LABELS)


def canonical_path(path):
    """Unicode and UTF-8 percent forms compare equally; encoded slashes stay distinct."""
    encoded = quote(path, safe="/:@!$&'()*+,;=-._~%")
    return re.sub(r"%([0-9a-f]{2})", lambda m: chr(int(m[1], 16))
                  if re.fullmatch(r"[A-Za-z0-9._~-]", chr(int(m[1], 16))) else "%" + m[1].upper(), encoded, flags=re.I)


def nonpublication_url(url):
    """Utility sections are not articles, even when a CMS stamps Article metadata on them.

    Only the first section (after an optional locale) is classified. A real story such
    as /news/privacy-policy-update remains eligible; contact/privacy/service sections
    and their CMS-generated child paths do not become dated company publications.
    """
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = [unquote(part).casefold().replace("_", "-") for part in urlsplit(url).path.split("/") if part]
    except (ValueError, UnicodeError):
        return True
    if parts and re.fullmatch(r"(?:no|nb|nn|en|sv|da|de|fr|es)(?:-[a-z]{2})?", parts[0]):
        parts.pop(0)
    root = re.sub(r"\.(?:html?|php|aspx?)$", "", parts[0]) if parts else ""
    return root in NONPUBLICATION_ROOTS


def functional_query(query):
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "msclkid"}]
    # Distinct parameter order is incidental; repeated parameter order can be functional.
    return tuple(sorted(pairs, key=lambda item: item[0]))


def publication_timestamp(value):
    """Preserve the source's timestamp and timezone, including RFC feed dates, without assuming local time."""
    return value if isinstance(value, str) and re.search(r"\d{1,2}:\d{2}", value) else None


def site_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _valid(year: int, month: int, day: int, today: str) -> str | None:
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    iso = value.isoformat()
    return iso if "1990-01-01" <= iso <= today else None


def find_date(text: str, today: str) -> tuple[str, str] | None:
    """(ISO date, the date text exactly as written) for the first valid date in `text`."""
    found = []
    for pattern, kind in DATE_PATTERNS:
        for match in pattern.finditer(text or ""):
            g = match.groups()
            if kind == "ymd":
                y, m, d = int(g[0]), int(g[1]), int(g[2])
            elif kind == "dmy":
                d, m, y = int(g[0]), int(g[1]), int(g[2])
            elif kind == "d_name_y":
                d, m, y = int(g[0]), MONTHS[g[1].lower()], int(g[2])
            else:
                m, d, y = MONTHS[g[0].lower()], int(g[1]), int(g[2])
            iso = _valid(y, m, d, today)
            if iso:
                found.append((match.start(), iso, match.group(0)))
    if not found:
        return None
    _, iso, written = min(found)
    return iso, written


def _iso_prefix(value: str, today: str) -> str | None:
    match = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", value or "")
    return _valid(int(match.group(1)), int(match.group(2)), int(match.group(3)), today) if match else None


class _Listing(HTMLParser):
    """Links, time elements and enclosing elements, with source offsets (for verbatim quotes). Bounded: at most
    MAX_DEPTH enclosing elements per position, MAX_ELEMENTS per page and TEXT_CAP characters of text each."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.stack, self.real, self.elements, self.links = [], [], {}, []
        self.anchor, self.open_time, self.skip = None, None, 0
        self.visibility = []
        self.next_id = 0

    def _here(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _tag_end(self, start):
        end = self.source.find(">", start)
        return len(self.source) if end < 0 else end + 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        here = self._here()
        hidden = ((self.visibility and self.visibility[-1][1])
                  or tag in {"script", "style", "template", "noscript", "footer", "aside", "nav", "form", "svg"}
                  or "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true"
                  or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style", ""), re.I))
                  or bool(ARTICLE_NOISE.search(attrs.get("class", "") + " " + attrs.get("id", ""))))
        if tag not in VOID:
            self.visibility.append((tag, bool(hidden)))
        if hidden:
            return
        if tag == "a":
            if _comment_fragment(attrs.get("href", "")):
                for eid in self.real:
                    self.elements[eid]["comment_link"] = True
            if attrs.get("href") is not None and self.anchor is None:
                self.anchor = {"href": attrs["href"], "text": [], "times": [], "ancestors": list(self.real),
                               "start": here, "end": None}
            return
        if tag == "time" and attrs.get("datetime"):
            tag_text = self.get_starttag_text() or ""
            entry = {"value": attrs["datetime"], "tag": tag_text, "start": here, "end": here + len(tag_text)}
            self.open_time = entry
            for eid in self.real:
                self.elements[eid]["times"].append(entry)
            if self.anchor is not None:
                self.anchor["times"].append(entry)
        if tag in VOID:
            return
        if len(self.real) < MAX_DEPTH and self.next_id < MAX_ELEMENTS:
            self.next_id += 1
            self.elements[self.next_id] = {"text": [], "size": 0, "times": [], "hrefs": set(), "start": here, "end": None, "comment_link": False}
            self.stack.append((tag, self.next_id))
            self.real.append(self.next_id)
        else:
            self.stack.append((tag, None))  # untracked, but keeps end tags matched to the right element

    def handle_endtag(self, tag):
        here = self._here()
        index = next((i for i in range(len(self.visibility) - 1, -1, -1) if self.visibility[i][0] == tag), None)
        if index is None:
            return
        hidden = self.visibility[index][1]
        del self.visibility[index:]
        if hidden:
            return
        if tag == "time" and self.open_time is not None:
            self.open_time["end"] = self._tag_end(here)
            self.open_time = None
        if tag == "a":
            if self.anchor is not None:
                self.anchor["end"] = self._tag_end(here)
                self.links.append(self.anchor)
                self.anchor = None
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for _, eid in self.stack[index:]:
                    if eid is not None:
                        self.elements[eid]["end"] = self._tag_end(here)
                        self.real.remove(eid)
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.visibility and self.visibility[-1][1]:
            return
        for eid in self.real:
            element = self.elements[eid]
            if element["size"] <= TEXT_CAP:
                element["text"].append(data)
                element["size"] += len(data)
        if self.anchor is not None and self.open_time is None:
            self.anchor["text"].append(data)  # A date inside the link is not part of its title.

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)


def _quote(html, start, end):
    """The verbatim source between two offsets, or None when it is too long to be one readable quote."""
    if start is not None and end is not None and 0 < end - start <= MAX_QUOTE:
        return html[start:end]
    return None


def _comment_fragment(url):
    try:
        fragment = unquote(urlsplit(url or "").fragment)
    except (ValueError, TypeError):
        return False
    return bool(re.search(r"(?:^|[-_/])(?:comments?|respond|reply|reviews?)(?:[-_/]|\d|$)", fragment, re.I))


def _same_site_url(href, page_url, site):
    href = (href or "").strip()
    if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#")) or _comment_fragment(href):
        return None
    absolute = urljoin(page_url, href)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or site_of(absolute) != site:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _title(link):
    title = " ".join("".join(link["text"]).split())
    if not (8 <= len(title) <= 200) or ACTION_LABEL.search(title) or CONVERSION_LABEL.search(title):
        return None
    only_date = find_date(title, "9999-12-31")
    return None if only_date and only_date[1].strip() == title.strip() else title


def extract_dated_links(html: str, page_url: str, today: str) -> list[dict]:
    html = html or ""
    parser = _Listing(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup yields what was parsed so far
        pass
    if parser.anchor is not None:
        parser.links.append(parser.anchor)
    site = site_of(page_url)
    page = urlsplit(page_url)
    ignored = {urlunsplit((page.scheme, page.netloc, page.path, page.query, "")), f"{page.scheme}://{page.netloc}/"}
    for link in parser.links:
        target = _same_site_url(link["href"], page_url, site)
        link["url"] = target if target and not nonpublication_url(target) and not urlsplit(target).path.lower().endswith(FILE_SUFFIXES) else None
        link["title"] = _title(link) if link["url"] else None
        # Every other page this element links to competes for its dates, whatever the link text says.
        if target and target not in ignored:
            for eid in link["ancestors"]:
                parser.elements[eid]["hrefs"].add(target)
    results, seen = [], set()
    for link in parser.links:
        url, title = link["url"], link["title"]
        if not url or not title or url in seen or url in ignored:
            continue
        found = None
        for entry in link["times"]:  # A time element inside the link itself.
            if modification_date(entry["tag"] + " " + re.sub(r"<[^>]+>", " ", html[max(0, entry["start"] - 80):entry["end"]])):
                continue
            iso = _iso_prefix(entry["value"], today)
            if iso and entry["tag"] in html:
                found = (iso, "time_element", _quote(html, link["start"], link["end"]))
                break
        if found is None:
            hit = find_date(title, today)
            if hit and hit[1] in html:
                found = (hit[0], "block_text", _quote(html, link["start"], link["end"]))
        # Otherwise the nearest enclosing element that carries a date and links to no other page.
        for eid in reversed(link["ancestors"]):
            if found is not None:
                break
            element = parser.elements[eid]
            if len(element["hrefs"]) > 1 or element["comment_link"]:
                break
            for entry in element["times"]:
                if modification_date(entry["tag"] + " " + re.sub(r"<[^>]+>", " ", html[max(0, entry["start"] - 80):entry["end"]])):
                    continue
                iso = _iso_prefix(entry["value"], today)
                if iso and entry["tag"] in html:
                    start = min(link["start"], entry["start"])
                    end = max(link["end"] or link["start"], entry["end"] or entry["start"])
                    found = (iso, "time_element", _quote(html, start, end))
                    break
            if found is None and element["size"] <= TEXT_CAP and not element["times"]:
                block_text = "".join(element["text"])
                hit = find_date(block_text, today)
                if hit and modification_date(block_text[:block_text.find(hit[1])]):
                    hit = None
                if hit and hit[1] in html:
                    quote = _quote(html, element["start"], element["end"])
                    found = (hit[0], "block_text", quote if quote and hit[1] in quote else None)
        if found is None:
            match = URL_DATE.search(urlsplit(url).path)
            if match:
                iso = _valid(int(match.group(1)), int(match.group(2)), int(match.group(3)), today)
                if iso and match.group(0) in html:
                    quote = _quote(html, link["start"], link["end"])
                    found = (iso, "url_path", quote if quote and match.group(0) in quote else None)
        if found is None or not found[2]:
            continue  # no date, or the link and its date cannot be quoted together
        seen.add(url)
        iso, source, span = found
        results.append({"title": title, "url": url, "published_on": iso, "date_source": source, "span": span})
    results.sort(key=lambda r: r["published_on"], reverse=True)
    return results[:10]


class _Article(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.scripts, self.times = {}, [], []
        self.in_script = self.in_h1 = self.in_title = False
        self.in_article = self.article_count = 0
        self.h1, self.title, self.script = [], [], []
        self.static_page, self.current_time, self.article_text = False, None, ""
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.static_page = self.static_page or static_page_template(tag, attrs)
        hidden = ((self.stack and self.stack[-1]["hidden"]) or tag in {"style", "template", "noscript", "nav", "aside", "footer", "form", "svg"}
                  or "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true"
                  or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style", ""), re.I))
                  or bool(ARTICLE_NOISE.search(attrs.get("class", "") + " " + attrs.get("id", ""))))
        context = ""
        for parent in reversed(self.stack):
            if parent["tail"].strip():
                context = parent["tail"][-80:]
                break
            if parent["tag"] in ARTICLE_BLOCKS:
                break
        if tag not in VOID:
            if len(self.stack) >= MAX_DEPTH:
                raise ValueError("Article markup exceeds nesting limit")
            self.stack.append({"tag": tag, "hidden": bool(hidden), "tail": ""})
        if hidden:
            return
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
            if key and attrs.get("content") is not None:
                self.meta.setdefault(key, attrs["content"])
        elif tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.in_script, self.script = True, []
        elif tag == "article":
            self.in_article += 1
            self.article_count += 1
        elif tag == "time" and self.in_article and attrs.get("datetime"):
            props = str(attrs.get("itemprop", "")).lower().split()
            self.current_time = {"value": attrs["datetime"], "tag": self.get_starttag_text(),
                                 "context": context, "text": [], "closed": False,
                                 "publication_property": "datepublished" in props,
                                 "other_date_property": bool(set(props) & {"datemodified", "startdate", "enddate"})}
            self.times.append(self.current_time)
        elif tag == "h1":
            self.in_h1 = True
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if index is None:
            return
        closed = self.stack[index:]
        del self.stack[index:]
        hidden = closed[0]["hidden"]
        if hidden:
            return
        if tag in ARTICLE_BLOCKS:
            # A publication label in a different paragraph must not label an event's time.
            for parent in self.stack:
                parent["tail"] = ""
        if tag == "script" and self.in_script:
            self.scripts.append("".join(self.script))
            self.in_script = False
        elif tag == "article" and self.in_article:
            self.in_article -= 1
        elif tag == "h1":
            self.in_h1 = False
        elif tag == "title":
            self.in_title = False
        elif tag == "time":
            if self.current_time is not None:
                self.current_time["closed"] = True
            self.current_time = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack and self.stack[-1]["hidden"]:
            return
        if self.in_script:
            self.script.append(data)
            return
        if any(e["tag"] == "script" for e in self.stack):
            return
        for parent in self.stack:
            parent["tail"] = (parent["tail"] + data)[-160:]
        if self.in_article:
            self.article_text = (self.article_text + data)[-1000:]
        if self.current_time is not None:
            self.current_time["text"].append(data)
        if self.in_h1:
            self.h1.append(data)
        if self.in_title:
            self.title.append(data)


def _article_nodes(data, depth=0):
    if depth > 20:
        return
    if isinstance(data, list):
        for item in data[:200]:
            yield from _article_nodes(item, depth + 1)
    elif isinstance(data, dict):
        types = data.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(isinstance(t, str) and t in ARTICLE_TYPES for t in types):
            yield data
        if "@graph" in data:
            yield from _article_nodes(data["@graph"], depth + 1)


def _node_url(node, page_url=None):
    for key in ("url", "mainEntityOfPage", "@id"):
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        if isinstance(value, str) and value.strip() and page_url:
            try:
                if not urlsplit(value).scheme:
                    resolved = urljoin(page_url, value)
                    if urlsplit(resolved).scheme in {"http", "https"}:
                        return resolved
            except ValueError:
                continue
    return None


def _same_page(url, page_url):
    a, b = urlsplit(url), urlsplit(page_url)
    return (site_of(url) == site_of(page_url) and canonical_path(a.path).rstrip("/") == canonical_path(b.path).rstrip("/")
            and functional_query(a.query) == functional_query(b.query))


def _compatible_headline(headline, title):
    if not isinstance(headline, str) or not headline.strip() or not title:
        return True
    normalized = lambda value: " ".join(re.findall(r"\w+", value.casefold()))
    if normalized(headline) == normalized(title):
        return True
    # Publishers commonly suffix their name to the visible page title.
    return any(normalized(headline) == normalized(part) for part in re.split(r"\s+[|\u2013\u2014-]\s+", title)[:1])


def article_published_date(html: str, today: str = "9999-12-31", page_url: str | None = None) -> dict | None:
    """The page's own publication date: page-level meta, a JSON-LD article node about this page (or the only one),
    or the time element of the page's single <article>."""
    if nonpublication_url(page_url):
        return None
    parser = _Article()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001
        return None
    title = " ".join((parser.meta.get("og:title") or "".join(parser.h1) or "".join(parser.title)).split())
    site_name = " ".join(parser.meta.get("og:site_name", "").split())
    if site_name:
        title = re.sub(r"\s+[|–—-]\s+" + re.escape(site_name) + r"$", "", title, flags=re.I)
    nodes = []
    for raw in parser.scripts:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        nodes.extend((node, raw) for node in _article_nodes(data))
    own_nodes = [(node, raw) for node, raw in nodes
                 if ((_node_url(node, page_url) and (not page_url or _same_page(_node_url(node, page_url), page_url)))
                     or (not any(node.get(key) for key in ("url", "mainEntityOfPage", "@id")) and len(nodes) == 1
                         and _compatible_headline(node.get("headline"), title)))]
    explicit_article = parser.meta.get("og:type", "").lower() == "article" or bool(own_nodes)
    strong_nodes = [node for node, _ in own_nodes if any(isinstance(kind, str) and kind in {"NewsArticle", "BlogPosting", "PressRelease"}
                    for kind in (node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")]))]
    if parser.static_page and not strong_nodes:
        return None
    for key in ("article:published_time", "pubdate", "publish-date", "dc.date.issued"):
        value = parser.meta.get(key)
        iso = _iso_prefix(value or "", today)
        if iso and value in html:
            return {"published_on": iso, "title": title, "source": "meta", "span": value,
                    "article_evidence": explicit_article, "published_at": publication_timestamp(value)}
    for node, raw in own_nodes:
        value = node.get("datePublished")
        if not isinstance(value, str):
            continue
        iso = _iso_prefix(value, today)
        if not iso:
            continue
        member = re.search(r'"datePublished"\s*:\s*"' + re.escape(value) + '"', raw)
        if member and member.group(0) in html:
            headline = node.get("headline") if isinstance(node.get("headline"), str) else ""
            return {"published_on": iso, "title": title or headline, "source": "jsonld", "span": member.group(0),
                    "article_evidence": True, "published_at": publication_timestamp(value)}
    if parser.article_count == 1:
        for entry in parser.times:
            value, tag_text = entry["value"], entry["tag"]
            text = "".join(entry["text"])
            if (not entry["closed"] or entry["other_date_property"] or not
                    (entry["publication_property"] or PUBLICATION_PREFIX.search(entry["context"])
                     or PUBLICATION_TEXT.search(text))):
                continue
            if modification_date(entry["context"] + " " + (tag_text or "") + " " + text):
                continue
            iso = _iso_prefix(value, today)
            if iso and tag_text and tag_text in html:
                return {"published_on": iso, "title": title, "source": "time_element", "span": tag_text,
                        "article_evidence": True, "published_at": publication_timestamp(value)}
    value = parser.meta.get("date")
    iso = _iso_prefix(value or "", today)
    if iso and value in html:
        return {"published_on": iso, "title": title, "source": "meta", "span": value,
                "article_evidence": explicit_article, "published_at": publication_timestamp(value)}
    return None
