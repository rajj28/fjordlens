"""Identity gate v2: decide whether a fetched site is the exact legal entity's own site.

Designed for zero wrong-company publications (one material mismatch blocks qualification).
Evidence classes (see planning/PLAN.md section 3):
  E1 org number in an owner position     E2 full legal name in owner strings (+ sister guard)
  E3 company-declared candidate          E4 registry contact (phone / street+postcode / e-mail)
  E5 conflicting owner org number        E6 parked, directory, social, foreign .com guess
Decision: A=E1 | B=E3+E2 | D=guess+E2+E4 ; anything else is ambiguous and never published.
"""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlsplit
from .core import org_number
from .html import structured_nodes

LEGAL_SUFFIXES = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "ks", "enk", "iks", "bbl", "brl", "sti", "kf", "fli", "ab", "ltd", "inc", "gmbh"}
DECLARED_ORIGINS = {"registry_website", "registry_email_domain", "registry_workplace_website", "registry_workplace_email_domain",
                    "wikidata_official_website", "nav_employer_homepage"}
# Declarations made by the company itself (registry entries, its own job ads); Wikidata is community-edited.
OWN_DECLARATIONS = {"registry_website", "registry_email_domain", "registry_workplace_website", "registry_workplace_email_domain",
                    "nav_employer_homepage"}
REGISTERED_SITES = {"registry_website", "registry_workplace_website"}
# Words that may stand right before a legal name in a title or (c) line without being part of it.
NAME_LEAD_INS = {"copyright", "velkommen", "welcome", "til", "to", "fra", "from", "om", "about", "kontakt", "contact", "logo",
                 "hjem", "home", "forside", "startside", "side", "nettside", "website"}
NON_SITE_HOSTS = {"proff.no", "purehelp.no", "1881.no", "gulesider.no", "brreg.no", "finn.no", "bedriftsdatabasen.no", "firmasok.no",
                  "facebook.com", "fb.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
                  "google.com", "google.no", "goo.gl", "sites.google.com", "wix.com", "wordpress.com", "blogspot.com", "mittanbud.no",
                  "anbudstorget.no", "legelisten.no", "yelp.com", "tripadvisor.com", "hotels.com", "booking.com", "vipps.no", "linktr.ee",
                  "hugedomains.com", "sedo.com", "dan.com", "afternic.com", "godaddy.com", "namecheap.com", "domainmarket.com",
                  "undeveloped.com", "bodis.com", "parkingcrew.net", "above.com", "uniregistry.com", "sav.com", "buydomains.com",
                  "domainnameshop.com", "efty.com", "squadhelp.com", "atom.com", "brandbucket.com", "parklogic.com"}
PARKED = ("domain is for sale", "domain for sale", "buy this domain", "hugedomains", "domenet er til salgs", "dette domenet er parkert",
          "this domain is parked", "parked domain", "domain parking", "denne domenen er registrert", "dette domenet er registrert",
          "registrert hos domeneshop", "hosted by one.com", "webhosting made simple", "this domain has been registered",
          "this site is under construction", "website coming soon", "nettstedet er under konstruksjon", "kommer snart",
          "her flytter snart en ny gjest", "default web site page", "it works!", "welcome to nginx", "apache2 ubuntu default page",
          "index of /", "account suspended", "this account has been suspended", "domain has expired", "domenet har utløpt",
          "registrar parking", "sedo domain", "dan.com", "afternic", " is parked", "parkert domene", "domenet er parkert",
          " is for sale", "this domain may be for sale", "domain may be for sale", "make an offer on this domain",
          "denne siden er under oppbygging", "siden er under arbeid", "nettsiden kommer", "coming soon")
STRONG_PARKED = ("is for sale", "domain is for sale", "buy this domain", "hugedomains", "domenet er til salgs", "is parked",
                 "parkert domene", "this domain may be for sale", "make an offer on this domain", "domain for sale")
ORG_DIGITS = re.compile(r"(?<![0-9])([0-9]{3})[ .   ]?([0-9]{3})[ .   ]?([0-9]{3})(?![0-9])")
ORG_LABEL = re.compile(r"org(?:anisasjon)?s?\.?\s*-?\s*(?:nr|nummer|no)\b|organi[sz]ation\s*(?:number|no)|foretaks(?:nummer|nr)|\bmva\b|\bvat\b|business\s*id", re.I)
ENTITY_PHRASE = re.compile(r"((?:[A-ZÆØÅ0-9][\w&'.\-]*\s+){0,6}?[A-ZÆØÅ0-9][\w&'.\-]*)\s+(AS|ASA|ANS|DA|SA|BA|NUF|KS)\b")
CREDIT = re.compile(r"levert av|utviklet av|laget av|designet av|design av|webdesign|nettside(?:r)? (?:av|fra|levert)|produsert av|driftet av|website by|web design|designed by|developed by|powered by|hosted by|made by|built by|i samarbeid med|webbyr[aå]|reklamebyr[aå]|leverand[oø]r av nettside", re.I)
GROUPISH = {"gruppen", "group", "konsern", "konsernet", "holding", "holdings"}
# Words that put our name inside a group's story ("one of our companies", "subsidiary"): rule C abstains.
GROUP_CONTEXT = GROUPISH | {"gruppe", "konsernets", "datterselskap", "datterselskaper", "datterselskapet", "datterselskapene", "selskapene",
                            "morselskap", "morselskapet", "eierselskap", "portefolje", "portefoljeselskap", "portefoljeselskaper",
                            "subsidiary", "subsidiaries", "companies", "portfolio"}
NORWEGIAN_WORDS = re.compile(r"\b(og|til|for|med|på|er|vi|kontakt|oss|tjenester|ansatte|bedrift|hjem|velkommen|telefon|adresse)\b", re.I)


def fold(value):
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def tokens(value):
    return re.findall(r"[a-z0-9]+", fold(value))


def name_tokens(legal_name):
    """Distinctive legal-name tokens (legal-form suffixes removed, order kept)."""
    found = [t for t in tokens(legal_name) if t not in LEGAL_SUFFIXES]
    return found or tokens(legal_name)


def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def registered_domain(url_or_host):
    parts = host(url_or_host if "://" in str(url_or_host) else "https://" + str(url_or_host)).split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "priv", "kommune", "gs", "vgs"} and parts[-1] in {"no", "uk"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def org_mentions(text):
    """Every valid organisation number in text with its position (checksum-validated)."""
    found = []
    for match in ORG_DIGITS.finditer(text or ""):
        try:
            found.append((org_number("".join(match.groups())), match.start(), match.end()))
        except ValueError:
            pass
    return found


def contains_tokens(haystack_tokens, needle):
    """All needle tokens appear as whole tokens, or the needle written together equals one whole
    token or a run of consecutive whole tokens ("BullKingClub" for BULLKING CLUB). Never a substring."""
    if not needle:
        return False
    hay = list(haystack_tokens)
    if all(t in set(hay) for t in needle):
        return True
    compact = "".join(needle)
    if len(compact) < 5:
        return False
    for i in range(len(hay)):
        run = ""
        for j in range(i, min(i + len(needle) + 2, len(hay))):
            run += hay[j]
            if run == compact:
                return True
            if len(run) >= len(compact) or not compact.startswith(run):
                break
    return False


def digits_pattern(digits):
    return re.compile(r"(?<!\d)(?:\+?47[\s.\-]*)?" + r"[\s.\-()]{0,2}".join(digits) + r"(?!\d)")


def norm_street(value):
    text = " ".join(tokens(value))
    for short, full in ((r"\bgt\b", "gate"), (r"\bvn\b", "veien"), (r"\bv\b", "vei"), (r"\bpl\b", "plass")):
        text = re.sub(short, full, text)
    return text


LEGAL_SUFFIX = re.compile(r"(?<![\w-])(AS|ASA|ANS|DA|SA|BA|NUF|KS)(?![\w-])")


def name_phrase_signals(text, ours):
    """(exact, superset) for legal-entity names in text. For every legal-form suffix, take the
    shortest run of words immediately before it that contains all our tokens: equal to our name
    means our exact legal name is written; extra words ("Bergen Bil Eiendom AS" for BERGEN BIL AS)
    mean a different, longer legal name containing ours."""
    exact = superset = False
    if not ours:
        return exact, superset
    for match in LEGAL_SUFFIX.finditer(text or ""):
        before = tokens(text[max(0, match.start() - 240):match.start()])
        for k in range(1, min(len(ours) + 6, len(before)) + 1):
            window = before[-k:]
            if all(t in window for t in ours):
                if window == list(ours):
                    exact = True
                else:
                    superset = True
                break
    return exact, superset


class Context:
    """Registry facts the gate may compare against (from the official stage only)."""
    def __init__(self, entity, workplaces=(), people=()):
        self.people = [t for t in (tokens(p) for p in people) if len(t) >= 2]
        self.org = entity["organisasjonsnummer"]
        self.name = entity.get("navn") or ""
        self.tokens = name_tokens(self.name)
        phones = []
        for key in ("telefon", "mobil"):
            digits = re.sub(r"\D", "", str(entity.get(key) or ""))
            digits = digits[2:] if digits.startswith("47") and len(digits) == 10 else digits
            if re.fullmatch(r"\d{8}", digits):
                phones.append(digits)
        self.addresses = []
        for address in [entity.get("forretningsadresse") or {}] + [w for w in workplaces if isinstance(w, dict)]:
            postcode = str(address.get("postnummer") or "")
            for street in address.get("adresse") or []:
                street = norm_street(street)
                if len(street) >= 6 and re.search(r"\d", street) and re.fullmatch(r"\d{4}", postcode):
                    self.addresses.append((street, postcode))
        for workplace in workplaces:
            for key in ("telefon", "mobil"):
                digits = re.sub(r"\D", "", str((workplace or {}).get(key) or ""))
                digits = digits[2:] if digits.startswith("47") and len(digits) == 10 else digits
                if re.fullmatch(r"\d{8}", digits):
                    phones.append(digits)
        self.phones = sorted(set(phones))
        self.email = str(entity.get("epostadresse") or "").strip().lower()
        self.emails = sorted({e for e in [self.email] + [str((w or {}).get("epostadresse") or "").strip().lower()
                                                         for w in workplaces if isinstance(w, dict)] if "@" in e})
        name_words = tokens(self.name)
        self.suffix = name_words[-1] if name_words and name_words[-1] in LEGAL_SUFFIXES else None


def _name_in(page_tokens, person):
    """Registered person's name on the page: the full registered name in order, or first and last
    name adjacent (middle names are often dropped on websites)."""
    n = len(person)
    for i in range(len(page_tokens) - 1):
        if page_tokens[i:i + n] == person:
            return True
        if page_tokens[i] == person[0] and page_tokens[i + 1] == person[-1]:
            return True
    return False


PLACEHOLDER_WORDS = {"www", "is", "parked", "for", "sale", "hjem", "home", "forside", "velkommen", "til", "coming", "soon",
                     "domain", "domene", "parkert", "registered", "registrert"}


def domain_placeholder(owner, url):
    """An owner string that spells out the site's own domain ("sognestal.no", "eltoro.no is parked")
    and says nothing else is a parking/placeholder title, not evidence of who owns the site."""
    domain = registered_domain(url)
    if domain.casefold() not in fold(owner):
        return False
    return not (set(tokens(owner)) - set(tokens(domain)) - PLACEHOLDER_WORDS)


def owner_strings(page):
    strings = [page.get("title", ""), page.get("h1", ""), page.get("meta", {}).get("og:site_name", ""),
               page.get("meta", {}).get("og:title", ""), page.get("meta", {}).get("application-name", "")]
    strings += page.get("logo_alts", [])
    strings += [line for line in page.get("lines", []) if "©" in line or re.search(r"copyright", line, re.I)][:10]
    for node, _ in structured_nodes(page.get("jsonld", [])):
        types = node.get("@type")
        types = {types} if isinstance(types, str) else set(t for t in types if isinstance(t, str)) if isinstance(types, list) else set()
        if types & {"Organization", "Corporation", "LocalBusiness", "ProfessionalService", "Store", "Restaurant", "WebSite", "AutoDealer",
                    "HomeAndConstructionBusiness", "GeneralContractor", "Electrician", "Plumber", "LegalService", "AccountingService"}:
            for key in ("name", "legalName", "alternateName"):
                if isinstance(node.get(key), str):
                    strings.append(node[key])
    return [s for s in strings if s]


OWNER_NODE_PATH = re.compile(r"^/\d+(?:/\d+)?(?:/@graph/\d+)?(?:/publisher)?$")


def structured_org_ids(page):
    """(org_number, node_name, parsed_html_pointer, raw_value) for the site's OWN organisation nodes:
    top-level nodes or a top-level node's publisher, whose url (if any) is on this site. Article
    subjects, mentions, reviews, members and other nested organisations are not ownership."""
    out = []
    site = registered_domain(page.get("url", ""))
    for node, path in structured_nodes(page.get("jsonld", [])):
        if not OWNER_NODE_PATH.match(path):
            continue
        node_url = node.get("url") if isinstance(node.get("url"), str) else None
        if node_url and node_url.startswith(("http://", "https://")) and registered_domain(node_url) != site:
            continue
        for key in ("taxID", "vatID", "identifier"):
            values = node.get(key)
            listed = isinstance(values, list)
            for index, value in enumerate(values if listed else [values]):
                pointer = "/jsonld" + path + "/" + key + (f"/{index}" if listed else "")
                if isinstance(value, dict):
                    if key == "identifier" and not re.search(r"org|vat|mva|foretak|business.?registration", str(value.get("propertyID", "")), re.I):
                        continue
                    value, pointer = value.get("value"), pointer + "/value"
                elif key == "identifier" and not re.fullmatch(r"\s*(?:NO)?\s*[0-9 .]{9,11}\s*(?:MVA)?\s*", str(value or ""), re.I):
                    continue
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    for number, _, _ in org_mentions(str(value)):
                        out.append((number, node.get("legalName") or node.get("name") or "", pointer, value))
    return out


def owner_mentions(text):
    """Org numbers in text, except those inside a supplier credit (e.g. \"Website by X AS, org nr ...\")."""
    found = set()
    for number, start, end in org_mentions(text or ""):
        if not CREDIT.search(text[max(0, start - 120):end + 20]):
            found.add(number)
    return found


def exact_owner_name(owners, ctx):
    """The owner string that writes our exact registered name with our own legal-form suffix and is not
    preceded by another capitalised word ("Nye Arona Trehus AS" is a different name)."""
    if not ctx.suffix:
        return None
    n = len(ctx.tokens)
    for owner in owners:
        for match in LEGAL_SUFFIX.finditer(owner):
            if match.group(1).lower() != ctx.suffix:
                continue
            words = list(re.finditer(r"\w+", owner[:match.start()]))
            if len(words) < n or [fold(w.group()) for w in words[-n:]] != list(ctx.tokens):
                continue
            if len(words) > n:
                # The name must start the string or follow a clear separator ("|", a spaced dash, "(c)",
                # a year) or a lead-in word ("Velkommen til"): "Hammer & X AS", "Hansen og X AS" and
                # "Nord-X AS" are different registered names.
                lead = words[-n - 1]
                between = owner[lead.end():words[-n].start()]
                separated = bool(re.fullmatch(r"\s*(?:[|:·•©]|\s[-–—]\s|\(c\))\s*", between)) or lead.group().isdigit()
                if not separated and fold(lead.group()) not in NAME_LEAD_INS:
                    continue
            return owner
    return None


def other_legal_names(pages, ctx):
    """Distinct other legal-entity names ("Optimera AS") written on the checked pages."""
    found = set()
    ours = list(ctx.tokens)
    for page in pages:
        text = page.get("text", "")
        for match in LEGAL_SUFFIX.finditer(text):
            words = re.findall(r"[^\W_][\w&.'-]*", text[max(0, match.start() - 80):match.start()])[-4:]
            run = []
            for word in reversed(words):
                if word[:1].isupper() or word[:1].isdigit():
                    run.insert(0, word)
                else:
                    break
            found_tokens = [t for t in tokens(" ".join(run)) if fold(t) not in NAME_LEAD_INS]
            if found_tokens and found_tokens[-len(ours):] != ours:
                found.add(" ".join(found_tokens))
    return found


def phrase_window(text, ours):
    """Short verbatim window of parsed page text around the first exact writing of our legal name."""
    for match in LEGAL_SUFFIX.finditer(text or ""):
        before = tokens(text[max(0, match.start() - 240):match.start()])
        for k in range(1, min(len(ours) + 6, len(before)) + 1):
            window = before[-k:]
            if all(t in window for t in ours):
                if window == list(ours):
                    return text[max(0, match.start() - 160):match.end() + 40].strip()
                break
    return None


def group_context(ctx, url, owners, pages):
    """True when a group frames the site: group words in an owner string or the domain label, or
    around the exact writing of our legal name ("Holmen Bygg AS er et av selskapene i gruppen")."""
    ours = set(ctx.tokens)
    if any((set(tokens(o)) & GROUPISH) - ours for o in owners):
        return True
    label = registered_domain(url).split(".")[0]
    if any(word in label and word not in ours for word in GROUPISH):
        return True
    windows = (phrase_window(p.get("text", ""), ctx.tokens) for p in pages)
    return any((set(tokens(w)) & GROUP_CONTEXT) - ours for w in windows if w)


def text_window(text, number):
    """Short verbatim window of parsed page text around the first mention of `number`."""
    for found, start, end in org_mentions(text):
        if found == number:
            return text[max(0, start - 60):end + 20].strip()
    return None


def assess(ctx, candidate, pages):
    """Return a JSON-serializable assessment. pages: parsed same-site pages, homepage first."""
    url = pages[0]["url"] if pages else candidate["url"]
    result = {"organisation_number": ctx.org, "candidate_url": candidate["url"], "url": url, "origin": candidate["origin"],
              "method": "identity_gate_v2", "decision": "ambiguous", "publishable": False, "hard_reject": False,
              "signals": {}, "reasons": []}
    reasons, signals = result["reasons"], result["signals"]

    def reject(why):
        result["hard_reject"] = True
        reasons.append(why)
        return result

    if not pages:
        return reject("no page could be fetched")
    h = host(url)
    if any(h == d or h.endswith("." + d) for d in NON_SITE_HOSTS):
        return reject("directory, marketplace, builder or social host is not a company-owned site")
    home = pages[0]
    low = home.get("text", "").lower()
    head = (home.get("title", "") + " " + home.get("text", "")[:600]).lower()
    if any(marker in head for marker in STRONG_PARKED) or (any(marker in low for marker in PARKED) and len(home.get("text", "")) < 4000):
        return reject("parked, for-sale, placeholder, suspended or default hosting page")

    guessed = candidate["origin"] in {"dns_guess", "dns_guess_brand"}
    label = registered_domain(candidate["url"]).split(".")[0].replace("-", "")
    final_host = host(url).replace("-", "")
    # Redirecting to another registered domain is suspicious for a guessed name (parking, resale,
    # another company) unless the destination host still carries the name ("x.webnode.page").
    cross_domain = registered_domain(url) != registered_domain(candidate["url"]) and not (len(label) >= 4 and label in final_host)
    signals["redirected_to_other_domain"] = cross_domain
    # E2 first (it qualifies subpage proofs): full legal name in the HOMEPAGE owner strings.
    owners = [o for o in owner_strings(home) if not domain_placeholder(o, url)]
    name_hit = next((o for o in owners if contains_tokens(tokens(o), ctx.tokens)), None)
    if len(ctx.tokens) == 1 and name_hit:
        token = ctx.tokens[0]
        if len(token) < 3 or (len(token) < 5 and token not in registered_domain(url).split(".")[0]):
            name_hit = None  # Very short single-token names need the domain to carry the name too.
    all_text = " ".join(p.get("text", "") for p in pages)
    phrase_hits = [name_phrase_signals(p.get("text", ""), ctx.tokens) for p in pages]
    exact_phrase = any(e for e, _ in phrase_hits)
    superset = any(sup for _, sup in phrase_hits)
    owner_signals = [name_phrase_signals(o, ctx.tokens) for o in owners]
    owner_exact = any(e for e, _ in owner_signals)
    owner_superset = any(sup for _, sup in owner_signals)
    groupish = bool(name_hit) and bool((set(tokens(name_hit)) & GROUPISH) - set(ctx.tokens))
    name_owner = (bool(name_hit) and not groupish and not (owner_superset and not owner_exact)
                  and (exact_phrase or owner_exact or not superset))
    signals.update(owner_names_longer_legal_name=owner_superset and not owner_exact, owner_string_group_word=groupish)
    signals.update(name_in_owner_strings=bool(name_hit), owner_string=(name_hit or "")[:200], exact_legal_phrase_on_site=exact_phrase,
                   longer_legal_name_containing_ours=superset, name_owner=name_owner)
    if name_hit and superset and not exact_phrase:
        reasons.append("a longer company name containing ours appears and ours does not (possible sister or namesake)")

    # E1 / E5: organisation numbers by position. Footer and the site's own organisation JSON-LD are
    # owner positions; a labelled number in body text (e.g. a group's list of subsidiaries) is not.
    per_page, strong_all, labels_all, all_numbers = [], set(), set(), set()
    for page in pages:
        footer = owner_mentions(page.get("footer", ""))
        structured = {}
        for number, node_name, pointer, raw in structured_org_ids(page):
            all_numbers.add(number)
            if number == ctx.org and node_name and not contains_tokens(tokens(node_name), ctx.tokens):
                continue  # Our number inside another named organisation is a mention, not ownership.
            structured.setdefault(number, (pointer, raw))
        labels = {n for line in page.get("lines", []) if ORG_LABEL.search(line) for n in owner_mentions(line)}
        all_numbers |= footer | labels | owner_mentions(page.get("text", ""))
        strong_all |= footer | set(structured)
        labels_all |= labels
        per_page.append((footer, structured, labels))
    signals.update(org_anywhere=ctx.org in all_numbers, owner_position_org_numbers=sorted(strong_all)[:10],
                   distinct_org_numbers=len(all_numbers))
    if (strong_all - {ctx.org}) and ctx.org not in strong_all:
        return reject("another legal entity's organisation number is in the site's owner position")
    if (labels_all - {ctx.org}) and ctx.org not in all_numbers:
        return reject("the site labels another legal entity's organisation number and never shows ours")
    if ctx.org in strong_all and (strong_all - {ctx.org}):
        return reject("several legal entities share the owner position (group or multi-company site)")
    home_footer, home_structured, _ = per_page[0]
    home_strong = ctx.org in home_footer or ctx.org in home_structured
    sub_strong = any(ctx.org in f or ctx.org in st for f, st, _ in per_page[1:]) or (all_numbers == {ctx.org} and ctx.org in labels_all)
    org_owner = home_strong or (sub_strong and name_owner)
    signals.update(org_on_homepage_owner_position=home_strong, org_on_subpage=sub_strong, org_owner=org_owner)

    declared = candidate["origin"] in DECLARED_ORIGINS
    guess = candidate["origin"] == "dns_guess"  # Brand guesses ("dns_guess_brand") can only pass by rule A.
    # E4: registry contact corroboration (non-circular).
    page_digits = " ".join([all_text] + [c for p in pages for c in p.get("contacts", [])])
    phone_hit = next((ph for ph in ctx.phones if digits_pattern(ph).search(page_digits)), None)
    folded = " ".join(tokens(all_text))
    address_hit = next(((st, pc) for st, pc in ctx.addresses if st in folded and re.search(r"(?<!\d)" + pc + r"(?!\d)", all_text)), None)
    # An e-mail at the candidate's own domain is circular when that domain came from the e-mail.
    candidate_domain = registered_domain(candidate["url"])
    email_hit = any(e in page_digits.lower() for e in ctx.emails
                    if not (candidate["origin"].endswith("email_domain") and registered_domain("http://" + e.rsplit("@", 1)[-1]) == candidate_domain))
    signals.update(registry_phone_on_site=bool(phone_hit), registry_address_on_site=bool(address_hit), registry_email_on_site=email_hit)
    page_tokens = tokens(all_text)
    # A person counts as independent evidence only if a name token we must match on the page (first
    # or last name) is not part of the company's own name: "Daniel Olsen" proves nothing for DANIEL
    # OLSEN AS, while "Kjetil Bjune" still identifies a specific person for BYGGMESTER BJUNE AS.
    independent = [person for person in ctx.people if {person[0], person[-1]} - set(ctx.tokens)]
    person_hit = next((" ".join(person) for person in independent if _name_in(page_tokens, person)), None)
    signals["registered_person_on_site"] = bool(person_hit)
    contact = bool(phone_hit or address_hit or email_hit)


    if guess and registered_domain(url).endswith(".com") and not org_owner:
        norwegian = home.get("lang", "").startswith(("no", "nb", "nn")) or len(NORWEGIAN_WORDS.findall(home.get("text", "")[:20000])) >= 8
        signals["norwegian_site"] = norwegian
        if not norwegian:
            return reject("non-Norwegian .com site without our organisation number")

    if not org_owner and len(home.get("text", "")) < 80 and not home.get("links") and not (declared and name_owner):
        return reject("homepage has no substantive content and no organisation-number proof")
    if guessed and cross_domain and not org_owner:
        return reject("guessed domain redirects to a different domain without our organisation number")
    multi_entity = bool(all_numbers - {ctx.org})
    signals["multi_entity_site"] = multi_entity
    if multi_entity and not org_owner:
        reasons.append("the site shows other legal entities' organisation numbers; only an organisation-number proof can accept it")
    rule = None
    if org_owner:
        rule = "A: our organisation number is in the site's owner position"
    elif declared and name_owner and not multi_entity:
        rule = "B: company-declared site shows the full legal name in its owner strings"
    elif guess and name_owner and contact and not multi_entity:
        rule = "D: guessed domain shows the full legal name and registry contact details"
    elif guess and name_owner and person_hit and not multi_entity:
        rule = "D2: guessed domain shows the full legal name and the registered CEO/chair/owner by full name"
    elif (guess and name_owner and not superset and not multi_entity and exact_owner_name(owners, ctx) and len(other_legal_names(pages, ctx)) < 2
          and urlsplit(url).hostname in {registered_domain(url), "www." + registered_domain(url)}):
        # Registered company names are unique in Norway, so our exact name WITH our legal form in the
        # site's own title, site name, logo or (c) line identifies us; chain subdomains are excluded.
        rule = "D3: guessed domain's owner strings carry our exact registered name with its legal form"
    elif (candidate["origin"] in OWN_DECLARATIONS and signals["org_anywhere"] and all_numbers == {ctx.org}
          and exact_phrase and not superset and (contact or person_hit)):
        rule = "A2: company-declared site shows our organisation number (no other), the exact legal name and registry contact details"
    elif (candidate["origin"] in REGISTERED_SITES and exact_phrase and not superset and not multi_entity and not groupish
          and not signals["owner_names_longer_legal_name"] and (contact or person_hit) and not group_context(ctx, url, owners, pages)
          and len(other_legal_names(pages, ctx)) < 2):
        # The company itself registered this site; the exact legal name plus a registry contact or a
        # registered person on it separate its own site from a parent's site that only shares contacts.
        rule = "C: registry-declared site shows the exact legal name and registry contact details or a registered person"
    if not rule:
        if not name_owner and not signals["org_anywhere"]:
            reasons.append("neither the legal name nor the organisation number identifies the site owner")
        elif guess and not contact:
            reasons.append("guessed domain shows the name but no registry contact detail or organisation number")
        elif signals["org_anywhere"]:
            reasons.append("organisation number appears only outside an owner position")
        return result
    result.update(decision="exact", publishable=True, scope="exact_legal_entity", rule=rule)
    # Strongest proof location first: homepage JSON-LD, homepage footer, then subpages; else the name.
    proof = None
    if org_owner:
        for index in range(len(pages)):
            footer, structured, labels = per_page[index]
            if ctx.org in structured:
                pointer, raw = structured[ctx.org]
                proof = (index, {"type": "parsed_html_pointer", "value": pointer}, str(raw))
                break
            if ctx.org in footer or ctx.org in labels:
                window = text_window(pages[index].get("text", ""), ctx.org)
                if window:
                    proof = (index, {"type": "normalized_text", "value": window}, window)
                    break
    if proof is None and rule.startswith("A2"):
        for index, page in enumerate(pages):
            window = text_window(page.get("text", ""), ctx.org)
            if window:
                proof = (index, {"type": "normalized_text", "value": window}, window)
                break
    if proof is None and rule.startswith("C"):
        for index, page in enumerate(pages):
            window = phrase_window(page.get("text", ""), ctx.tokens)
            if window:
                proof = (index, {"type": "normalized_text", "value": window}, window)
                break
    if proof is None:
        proof = (0, {"type": "normalized_text", "value": name_hit or ""}, name_hit or "")
    result["proof_page_index"], result["proof_selector"], result["proof_span"] = proof
    result["proof_url"] = pages[proof[0]]["url"]
    return result
