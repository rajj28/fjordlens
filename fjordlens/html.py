"""Bounded, dependency-free HTML and structured-data extraction."""
from __future__ import annotations
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, urldefrag

def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

class PageParser(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = self.base = url
        self.parts, self.title_parts, self.links, self.feeds, self.jsonld, self.contacts = [], [], [], [], [], []
        self.meta, self.canonical, self.depth, self.hidden, self.in_title = {}, "", 0, [], False
        self.anchor, self.script = None, None
        # Owner-position signals for the identity gate.
        self.footer_parts, self.h1_parts, self.logo_alts, self.lang = [], [], [], ""
        self.jsonld_raw = []
        self.open_counts, self.footer_marks, self.in_h1 = {}, [], 0

    def absolute(self, value):
        value = urldefrag(urljoin(self.base, value or ""))[0]
        return value if urlsplit(value).scheme in {"http", "https"} else ""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    FOOTERISH = re.compile(r"footer|bunn|site-info|copyright|colophon", re.I)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html" and not self.lang:
            self.lang = (a.get("lang") or "").lower()[:10]
        if tag not in self.VOID:
            self.open_counts[tag] = self.open_counts.get(tag, 0) + 1
            if not self.hidden and (tag == "footer" or self.FOOTERISH.search(" ".join(filter(None, (a.get("id"), a.get("class")))) or "")):
                self.footer_marks.append((tag, self.open_counts[tag]))
        if tag == "h1" and not self.hidden:
            self.in_h1 += 1
        if tag == "img" and not self.hidden and len(self.logo_alts) < 20:
            hint = " ".join(filter(None, (a.get("class"), a.get("id"), a.get("src"))))
            if a.get("alt") and re.search(r"logo", hint, re.I):
                self.logo_alts.append(clean(a["alt"])[:200])
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.hidden.append(tag)
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.script = []
        if self.hidden:
            return
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = (a.get("content") or "")[:10000]
        if tag == "base" and a.get("href"):
            candidate = self.absolute(a["href"])
            if urlsplit(candidate).hostname == urlsplit(self.url).hostname:
                self.base = candidate
        if tag == "link":
            rel = (a.get("rel") or "").lower().split()
            target = self.absolute(a.get("href"))
            if "canonical" in rel and target:
                self.canonical = target
            if "alternate" in rel and "xml" in (a.get("type") or "") and target:
                self.feeds.append(target)
        if tag == "a":
            self.end_anchor()
            if str(a.get("href", "")).lower().startswith(("mailto:", "tel:")) and len(self.contacts) < 100:
                self.contacts.append(a["href"])
            target = self.absolute(a.get("href")) if a.get("href") else ""
            if target:
                self.anchor = {"url": target, "href": a.get("href") or "", "parts": [], "rel": a.get("rel") or ""}
        if tag in {"p", "div", "br", "li", "footer", "header", "section", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.open_counts.get(tag):
            if self.footer_marks and self.footer_marks[-1] == (tag, self.open_counts[tag]):
                self.footer_marks.pop()
            self.open_counts[tag] -= 1
        if tag == "h1" and self.in_h1:
            self.in_h1 -= 1
        if tag == "script" and self.script is not None:
            if len(self.jsonld) < 100:
                try:
                    raw = "".join(self.script).strip()
                    self.jsonld.append(json.loads(raw))
                    self.jsonld_raw.append(raw)
                except (ValueError, RecursionError):
                    pass
            self.script = None
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]
        if self.hidden:
            return
        if tag == "title":
            self.in_title = False
        if tag == "a":
            self.end_anchor()
        if tag in {"p", "div", "li", "footer", "section", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data[:500000])
        if self.hidden:
            return
        self.parts.append(data)
        if self.footer_marks:
            self.footer_parts.append(data)
        if self.in_h1:
            self.h1_parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
        if self.anchor:
            self.anchor["parts"].append(data)

    def end_anchor(self):
        if self.anchor and len(self.links) < 3000:
            self.links.append({"url": self.anchor["url"], "href": self.anchor.get("href", ""), "text": clean(" ".join(self.anchor["parts"]))[:600], "rel": self.anchor["rel"]})
        self.anchor = None

def parse_html(html, url):
    parser = PageParser(url)
    try:
        parser.feed(html[:3_000_000])
        parser.close()
    except (ValueError, RecursionError):
        pass
    parser.end_anchor()
    unique = {}
    for link in parser.links:
        if link["url"] not in unique or not unique[link["url"]]["text"]:
            unique[link["url"]] = link
    return {"url": url, "title": clean(" ".join(parser.title_parts))[:600],
            "description": parser.meta.get("description") or parser.meta.get("og:description") or "",
            "text": clean(" ".join(parser.parts))[:500000], "lines": [clean(line) for line in "".join(parser.parts).splitlines() if clean(line)],
            "links": list(unique.values()), "jsonld": parser.jsonld, "jsonld_raw": parser.jsonld_raw, "feeds": list(dict.fromkeys(parser.feeds)),
            "canonical": parser.canonical, "meta": parser.meta, "contacts": list(dict.fromkeys(parser.contacts)),
            "footer": clean(" ".join(parser.footer_parts))[:20000], "h1": clean(" ".join(parser.h1_parts))[:600],
            "logo_alts": list(dict.fromkeys(parser.logo_alts)), "lang": parser.lang}

def structured_nodes(data, path="", depth=0):
    if depth > 20:
        return
    if isinstance(data, dict):
        if "@type" in data:
            yield data, path
        for key, value in data.items():
            yield from structured_nodes(value, path + "/" + key.replace("~", "~0").replace("/", "~1"), depth + 1)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            yield from structured_nodes(value, path + f"/{i}", depth + 1)
