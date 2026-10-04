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
from urllib.parse import urljoin, urlsplit, urlunsplit

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
        if tag in {"script", "style", "template", "noscript"}:
            self.skip += 1
            return
        if tag == "a":
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
            self.elements[self.next_id] = {"text": [], "size": 0, "times": [], "hrefs": set(), "start": here, "end": None}
            self.stack.append((tag, self.next_id))
            self.real.append(self.next_id)
        else:
            self.stack.append((tag, None))  # untracked, but keeps end tags matched to the right element

    def handle_endtag(self, tag):
        here = self._here()
        if tag in {"script", "style", "template", "noscript"}:
            self.skip = max(0, self.skip - 1)
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
        if self.skip:
            return
        for eid in self.real:
            element = self.elements[eid]
            if element["size"] <= TEXT_CAP:
                element["text"].append(data)
                element["size"] += len(data)
        if self.anchor is not None and self.open_time is None:
            self.anchor["text"].append(data)  # A date inside the link is not part of its title.


def _quote(html, start, end):
    """The verbatim source between two offsets, or None when it is too long to be one readable quote."""
    if start is not None and end is not None and 0 < end - start <= MAX_QUOTE:
        return html[start:end]
    return None


def _same_site_url(href, page_url, site):
    href = (href or "").strip()
    if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    absolute = urljoin(page_url, href)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or site_of(absolute) != site:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _title(link):
    title = " ".join("".join(link["text"]).split())
    if not (8 <= len(title) <= 200):
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
        link["url"] = target if target and not urlsplit(target).path.lower().endswith(FILE_SUFFIXES) else None
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
            if len(element["hrefs"]) > 1:
                break
            for entry in element["times"]:
                iso = _iso_prefix(entry["value"], today)
                if iso and entry["tag"] in html:
                    start = min(link["start"], entry["start"])
                    end = max(link["end"] or link["start"], entry["end"] or entry["start"])
                    found = (iso, "time_element", _quote(html, start, end))
                    break
            if found is None and element["size"] <= TEXT_CAP:
                hit = find_date("".join(element["text"]), today)
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

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
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
            self.times.append((attrs["datetime"], self.get_starttag_text()))
        elif tag == "h1":
            self.in_h1 = True
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self.in_script:
            self.scripts.append("".join(self.script))
            self.in_script = False
        elif tag == "article" and self.in_article:
            self.in_article -= 1
        elif tag == "h1":
            self.in_h1 = False
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_script:
            self.script.append(data)
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


def _node_url(node):
    for key in ("url", "mainEntityOfPage", "@id"):
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _same_page(url, page_url):
    a, b = urlsplit(url), urlsplit(page_url)
    return site_of(url) == site_of(page_url) and a.path.rstrip("/") == b.path.rstrip("/")


def article_published_date(html: str, today: str = "9999-12-31", page_url: str | None = None) -> dict | None:
    """The page's own publication date: page-level meta, a JSON-LD article node about this page (or the only one),
    or the time element of the page's single <article>."""
    parser = _Article()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    title = " ".join((parser.meta.get("og:title") or "".join(parser.h1) or "".join(parser.title)).split())
    for key in ("article:published_time", "date", "pubdate", "publish-date", "dc.date.issued"):
        value = parser.meta.get(key)
        iso = _iso_prefix(value or "", today)
        if iso and value in html:
            return {"published_on": iso, "title": title, "source": "meta", "span": value}
    nodes = []
    for raw in parser.scripts:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        nodes.extend((node, raw) for node in _article_nodes(data))
    for node, raw in nodes:
        url = _node_url(node)
        if (url and page_url and not _same_page(url, page_url)) or (not url and len(nodes) != 1):
            continue  # an article node about another page, or one of several unattributed nodes
        value = node.get("datePublished")
        if not isinstance(value, str):
            continue
        iso = _iso_prefix(value, today)
        if not iso:
            continue
        member = re.search(r'"datePublished"\s*:\s*"' + re.escape(value) + '"', raw)
        if member and member.group(0) in html:
            headline = node.get("headline") if isinstance(node.get("headline"), str) else ""
            return {"published_on": iso, "title": title or headline, "source": "jsonld", "span": member.group(0)}
    if parser.article_count == 1:
        for value, tag_text in parser.times:
            iso = _iso_prefix(value, today)
            if iso and tag_text and tag_text in html:
                return {"published_on": iso, "title": title, "source": "time_element", "span": tag_text}
    return None
