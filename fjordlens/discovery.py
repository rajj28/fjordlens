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
    toks = name_tokens(legal_name)
    if not toks:
        return []
    out = []
    joined, hyphen = "".join(toks), "-".join(toks)
    out.append(joined + ".no")
    if len(toks) > 1:
        out.append(hyphen + ".no")
    core = [t for t in toks if t not in GENERIC]
    if core and core != toks and len("".join(core)) >= 6:
        out.append("".join(core) + ".no")
    if re.search(r"[æøåÆØÅ]", legal_name or ""):
        alt = name_tokens(_alt_fold(legal_name))
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
    valid = [d for d in dict.fromkeys(out) if 3 <= len(d.split(".")[0]) <= 63 and re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", d)]
    return valid[:limit]


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
        legal = set(name_tokens(entity.get("navn")))
        for brand in brands or []:
            tokens_ = name_tokens(brand)
            if not tokens_ or set(tokens_) <= legal or re.search(r"\bavd(eling)?\b|\bfilial\b|\bkontor\b", brand, re.I):
                continue
            for name_domain in guess_domains(brand, 2):
                if registered_domain(name_domain) not in seen and name_domain.endswith(".no") and resolves(name_domain):
                    add("https://" + name_domain + "/", "dns_guess_brand", guessed_from="registered_workplace_name", brand=brand)
    return sites, profiles


def urlpath(url):
    return re.sub(r"^https?://[^/]+", "", url)
