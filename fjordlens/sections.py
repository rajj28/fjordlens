"""Nominate separately verifiable company sections on a registry-declared domain.

A link is a discovery hint, never identity evidence. A section gets no trusted-origin
credit and can be accepted only using its own A/D ownership proof. Root-page facts
and other company sections must not be mixed into that assessment or extraction.
"""
from __future__ import annotations
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from . import gate

ORIGIN = "registry_company_section"
ROOT_PATHS = {"", "/", "/index.html", "/index.htm", "/index.php", "/default.aspx"}
RELATION = re.compile(r"\b(?:customers?|clients?|suppliers?|references?|testimonials?|partners?|projects?|portfolio|cases?|case studies|"
                      r"kunder?|kundene|leverandorer?|referanser?|samarbeidspartnere?|prosjekter?|"
                      r"website by|designed by|developed by|powered by|levert av|utviklet av|laget av|design av)\b", re.I)
ASSET = re.compile(r"\.(?:pdf|docx?|xlsx?|zip|gz|jpe?g|png|gif|svg|webp|mp[34]|css|js|xml)$", re.I)
HIDDEN_STYLE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:[;\s]|$))", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _name(value):
    # Preserve every legal-name token and diacritic; only case/whitespace are immaterial.
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _relationship(value):
    return bool(RELATION.search(gate.fold(value).replace("_", " ").replace("-", " ")))


def _parts(url):
    try:
        parts = urlsplit(str(url or ""))
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return None
        if parts.port not in {None, 80, 443} or parts.query or parts.fragment:
            return None
        return parts
    except ValueError:
        return None


def _root(url):
    parts = _parts(url)
    return bool(parts and unquote(parts.path).casefold() in ROOT_PATHS)


def _section(url):
    parts = _parts(url)
    if not parts:
        return False
    path = unquote(parts.path)
    return bool(path.casefold() not in ROOT_PATHS and not ASSET.search(path) and not _relationship(path)
                and not any(segment in {".", ".."} for segment in path.split("/")))


class _Links(HTMLParser):
    """Text-labelled anchors, excluding explicit hidden and third-party-reference contexts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.links, self.recent = [], [], []
        self.anchor, self.heading, self.last_heading = None, None, ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (any(item[1] for item in self.stack) or tag in {"script", "style", "noscript", "template", "svg"}
                  or "hidden" in attrs or attrs.get("aria-hidden", "").casefold() == "true"
                  or bool(HIDDEN_STYLE.search(attrs.get("style") or ""))
                  or bool(set((attrs.get("class") or "").casefold().split()) & {"hidden", "sr-only", "visually-hidden"}))
        unrelated = tag == "article" or _relationship(" ".join(str(attrs.get(k) or "") for k in ("id", "class", "aria-label")))
        if tag not in VOID:
            self.stack.append((tag, hidden, unrelated))
        if hidden:
            return
        if tag in {"h1", "h2", "h3", "h4"}:
            self.heading = []
        if tag == "a":
            self._end_anchor()
            blocked = (any(item[2] for item in self.stack) or _relationship(self.last_heading)
                       or _relationship(" ".join(self.recent)[-180:]))
            self.anchor = {"href": attrs.get("href") or "", "parts": [], "blocked": blocked}

    def handle_endtag(self, tag):
        if tag == "a":
            self._end_anchor()
        if tag in {"h1", "h2", "h3", "h4"} and self.heading is not None:
            self.last_heading = " ".join(self.heading)
            self.heading = None
            self.recent = [self.last_heading]
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(item[1] for item in self.stack):
            return
        if self.anchor is not None:
            self.anchor["parts"].append(data)
        if self.heading is not None:
            self.heading.append(data)
        self.recent.append(data)
        self.recent = self.recent[-25:]

    def _end_anchor(self):
        if self.anchor is not None and len(self.links) < 3000:
            self.links.append({"href": self.anchor["href"], "text": " ".join(self.anchor["parts"]), "blocked": self.anchor["blocked"]})
        self.anchor = None


def nominate_company_sections(legal_name, parent_candidate, page, raw_html, limit=2):
    """Return at most two exact-name links from a declared root, with no identity credit.

    `page` is the parsed fetched root and `raw_html` its saved response text. Do not
    call this recursively on nominated sections or on generic contact/proof pages.
    """
    declared, fetched = parent_candidate.get("url", ""), page.get("url", "")
    if (parent_candidate.get("origin") not in gate.DECLARED_ORIGINS or not _root(declared) or not _root(fetched)
            or gate.host(declared) != gate.host(fetched) or not _name(legal_name)):
        return []
    parser = _Links()
    try:
        parser.feed(str(raw_html or "")[:3_000_000])
        parser.close()
        parser._end_anchor()
    except (ValueError, RecursionError):
        return []
    limit = min(2, max(0, int(limit)))
    found, seen = [], set()
    for link in parser.links:
        if len(found) >= limit:
            break
        if link["blocked"] or _name(link["text"]) != _name(legal_name):
            continue
        target = urljoin(fetched, link["href"])
        if not _section(target) or gate.host(target) != gate.host(fetched):
            continue
        parts = urlsplit(target)
        key = (gate.host(target), unquote(parts.path).rstrip("/"))
        if key in seen:
            continue
        seen.add(key)
        found.append({"url": urlunsplit(parts), "origin": ORIGIN, "parent_url": fetched,
                      "parent_origin": parent_candidate["origin"], "nomination_text": " ".join(link["text"].split())})
    return found


def assess_company_section(ctx, candidate, page):
    """Assess only the fetched section; require its own A or owner-name-plus-address D proof."""
    result = gate.assess(ctx, candidate, [page])
    same_section = (candidate.get("origin") == ORIGIN and _section(candidate.get("url")) and _section(page.get("url"))
                    and gate.host(candidate["url"]) == gate.host(page["url"]))
    if same_section:
        same_section = unquote(urlsplit(candidate["url"]).path).rstrip("/") == unquote(urlsplit(page["url"]).path).rstrip("/")
    rule = result.get("rule") or ""
    own_proof = rule.startswith("A:") or (rule.startswith("D:") and result["signals"].get("name_owner"))
    referral_title = _relationship(_name(page.get("title", "") + " " + page.get("h1", "")).replace(_name(ctx.name), ""))
    if not same_section or not own_proof or referral_title:
        result.update(publishable=False, decision="ambiguous")
        result.pop("rule", None)
        result.pop("scope", None)
        result["reasons"].append("Company sections require their own same-host section URL and A/D owner proof; root or referral facts are not ownership evidence")
    return result
