"""Candidate company-site discovery. A candidate is only a hypothesis; gate v2 decides."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import re
import socket
import threading
import unicodedata
from .gate import NON_SITE_HOSTS, host, name_tokens, registered_domain
from .net import public_unicast

CONSUMER_EMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "hotmail.co.uk", "outlook.com", "outlook.no", "live.com", "live.no",
    "msn.com", "yahoo.com", "yahoo.no", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "mail.com", "gmx.com", "gmx.net",
    "proton.me", "protonmail.com", "online.no", "frisurf.no", "broadpark.no", "lyse.net", "start.no", "getmail.no", "c2i.net",
    "chello.no", "altibox.no", "telia.no", "tele2.no", "eunet.no", "nextgentel.no", "enivest.net", "haugnett.no", "tussa.com",
    "svorka.net", "vestnett.no", "kvamnet.no", "signalbredband.no", "neasonline.no", "oddanett.no", "mimer.no", "bbnett.no",
    "tdcspace.dk", "sensewave.com", "ebnett.no", "rocketmail.com", "fastmail.com", "post.com", "hey.com", "zoho.com", "tutanota.com",
}
CONSUMER_EMAIL |= {"yahoo.se", "yahoo.dk", "yahoo.co.uk", "hotmail.se", "hotmail.dk", "outlook.se", "outlook.dk", "live.se", "live.dk",
                   "gmail.no", "icloud.no", "msn.no", "telenor.no", "gmx.de", "web.de", "t-online.de", "mail.ru", "yandex.ru", "qq.com", "163.com"}
SOCIAL = {"facebook.com": "Facebook", "fb.com": "Facebook", "instagram.com": "Instagram", "linkedin.com": "LinkedIn",
          "youtube.com": "YouTube", "youtu.be": "YouTube", "x.com": "X", "twitter.com": "X", "tiktok.com": "TikTok"}
GENERIC = {"holding", "holdings", "eiendom", "eiendommer", "invest", "investering", "gruppen", "group", "norge", "norway",
           "consulting", "consult", "service", "services", "drift", "utvikling", "management", "capital", "partners"}
# Only a domain nomination hint. These words are not removed from the identity gate's name.
DOMAIN_DESCRIPTORS = {"markedsforing", "arkitektfirma", "arkitektfirmaet", "advokatfirma", "advokatfirmaet",
                      "ingeniorfirma", "ingeniorfirmaet", "byggmester", "tannlege", "tannlegesenter", "frisor"}
_dns_cache, _dns_lock = {}, threading.Lock()
_dns_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="dns")


def normalize_url(value):
    value = str(value or "").strip().strip("/")
    if not value or "@" in value or " " in value or "." not in value:
        return None
    value = value if re.match(r"https?://", value, re.I) else "https://" + value
    return value if host(value) else None


def social_platform(url):
    h = host(url)
    return next((name for domain, name in SOCIAL.items() if h == domain or h.endswith("." + domain)), None)


def _lookup(domain):
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        return any(public_unicast(info[4][0]) for info in infos)
    except (OSError, UnicodeError, ValueError):
        return False


def resolves(domain, timeout=5.0):
    """Public DNS existence check (not an HTTP request). Cached per run."""
    with _dns_lock:
        if domain in _dns_cache:
            return _dns_cache[domain]
    try:
        ok = _dns_pool.submit(_lookup, domain).result(timeout=timeout)
    except Exception:
        ok = False
    with _dns_lock:
        _dns_cache[domain] = ok
    return ok


def _alt_fold(name):
    """Second spelling of Norwegian letters (ø→oe, å→aa, æ→ae) used by some domains."""
    text = str(name or "").translate(str.maketrans({"ø": "oe", "Ø": "OE", "å": "aa", "Å": "AA", "æ": "ae", "Æ": "AE"}))
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def guess_domains(legal_name, limit=6):
    if limit <= 0:
        return []
    toks = name_tokens(legal_name)
    if not toks:
        return []
    out = []
    joined, hyphen = "".join(toks), "-".join(toks)
    out.append(joined + ".no")
    if len(toks) > 1:
        out.append(hyphen + ".no")
    core = [t for t in toks if t not in GENERIC | DOMAIN_DESCRIPTORS]
    if core and core != toks and len("".join(core)) >= 4:
        out.append("".join(core) + ".no")
    # Initialisms are only hypotheses. Many multi-word legal names trade under three
    # or four initials; requiring a full name token in the host misses those sites.
    # Avoid two-letter combinations, which are especially broad and rarely useful.
    if 3 <= len(toks) <= 5 and all(len(t) > 1 and t.isalpha() for t in toks):
        out.append("".join(t[0] for t in toks) + ".no")
    if re.search(r"[æøåÆØÅ]", legal_name or ""):
        alt = name_tokens(_alt_fold(legal_name))
        alt_core = [alternate for original, alternate in zip(toks, alt) if original not in GENERIC | DOMAIN_DESCRIPTORS] if len(toks) == len(alt) else []
        # Apply the same descriptor-stripping hypothesis to the alternate spelling. It
        # remains one bounded nomination and gets no identity credit until fetched.
        if core != toks and alt_core and len("".join(alt_core)) >= 4 and "".join(alt_core) != "".join(core):
            out.append("".join(alt_core) + ".no")
        if alt and "".join(alt) != joined:
            out.append("".join(alt) + ".no")
        native = [t for t in re.findall(r"[0-9a-zæøå]+", (legal_name or "").casefold()) if t not in {"as", "asa", "ans", "da", "sa", "ba", "nuf", "ks"}]
        if native:
            try:
                out.append("".join(native).encode("idna").decode("ascii") + ".no")
            except UnicodeError:
                pass
    if len(joined) >= 6:
        out.append(joined + ".com")
    if re.search(r"(?:^|\s)as\s*$", legal_name or "", re.I):
        out.extend((joined + "as.no", hyphen + "-as.no"))
    valid = [d for d in dict.fromkeys(out) if 3 <= len(d.split(".")[0]) <= 63 and re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", d)]
    return valid[:limit]


def registered_brands(entity, brands=(), limit=2):
    """Bounded name hints from registered workplaces, without granting ownership.

    A workplace may be registered as ``LEGAL NAME AS AVD TRADING NAME``. Keep the
    useful suffix instead of discarding every name containing a branch marker.
    Known location-only suffixes and repeats of the legal name add no discovery value.
    """
    legal = set(name_tokens(entity.get("navn")))
    address = entity.get("forretningsadresse") or {}
    places = {tuple(name_tokens(address.get(key))) for key in ("poststed", "kommune")}
    places.discard(())
    out, seen = [], set()
    for value in brands or ():
        if not isinstance(value, str):
            continue
        name = " ".join(value.split())[:200]
        branch = re.split(r"\s+\b(?:avd(?:eling)?|filial|kontor)\.?\s+", name, maxsplit=1, flags=re.I)
        if len(branch) == 2:
            prefix_tokens = name_tokens(branch[0])
            suffix_tokens = name_tokens(branch[1])
            if prefix_tokens and not set(prefix_tokens) <= legal:
                name = branch[0].strip()  # TRADING NAME AVD TOWN retains the trading name.
            elif suffix_tokens and tuple(suffix_tokens) not in places:
                name = branch[1].strip()
            else:
                continue
        tokens_ = name_tokens(name)
        key = tuple(tokens_)
        if not tokens_ or set(tokens_) <= legal or key in places or key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= max(0, limit):
            break
    return out if limit > 0 else []


def candidates(entity, *, caches=None, dns=True, max_guesses=6, brands=None, workplaces=None):
    """Return (site_candidates, declared_profiles) in priority order. workplaces: registered subunit
    claim values; their registered websites and e-mail domains are company declarations too."""
    caches = caches or {}
    org = entity.get("organisasjonsnummer")
    workplaces = [w for w in workplaces or [] if isinstance(w, dict)]
    sites, profiles, seen = [], [], set()

    def add(url, origin, **extra):
        url = normalize_url(url)
        if not url:
            return
        if social_platform(url):
            profiles.append({"url": url, "platform": social_platform(url), "origin": origin, **extra})
            return
        h = host(url)
        if any(h == d or h.endswith("." + d) for d in NON_SITE_HOSTS):
            return
        key = registered_domain(url)
        if key in seen:
            return
        seen.add(key)
        sites.append({"url": url if url.endswith("/") or urlpath(url) else url + "/", "origin": origin, **extra})

    add(entity.get("hjemmeside"), "registry_website", source_pointer="/hjemmeside")
    email = str(entity.get("epostadresse") or "").strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain and domain not in CONSUMER_EMAIL and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        add("https://" + domain + "/", "registry_email_domain", source_pointer="/epostadresse")
    for workplace in workplaces:
        if workplace.get("website"):
            add(workplace["website"], "registry_workplace_website", workplace=workplace.get("organisation_number"))
    for workplace in workplaces:
        email = str(workplace.get("email") or "").strip().lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain and domain not in CONSUMER_EMAIL and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
            add("https://" + domain + "/", "registry_workplace_email_domain", workplace=workplace.get("organisation_number"))
    for url in caches.get("wikidata", {}).get(org, []):
        add(url, "wikidata_official_website")
    nav_homepages = caches.get("nav_homepages", {})
    for unit in [org] + [w.get("organisation_number") for w in workplaces]:
        for url in nav_homepages.get(unit, []):
            add(url, "nav_employer_homepage", employer_orgnr=unit)
    if dns:
        for name_domain in guess_domains(entity.get("navn"), max_guesses):
            if registered_domain(name_domain) in seen:
                continue
            if resolves(name_domain):
                add("https://" + name_domain + "/", "dns_guess", guessed_from="legal_name")
        # Trading names of registered workplaces ("RESTAURANT FJORD" under HOLDING X AS). Such a
        # site cannot carry the legal name, so only our organisation number in its owner position
        # (gate rule A) can ever accept it.
        for brand in registered_brands(entity, brands):
            for name_domain in guess_domains(brand, 2):
                if registered_domain(name_domain) not in seen and name_domain.endswith(".no") and resolves(name_domain):
                    add("https://" + name_domain + "/", "dns_guess_brand", guessed_from="registered_workplace_name", brand=brand)
    return sites, profiles


def urlpath(url):
    return re.sub(r"^https?://[^/]+", "", url)
