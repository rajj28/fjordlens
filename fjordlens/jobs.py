"""Hiring evidence from NAV's official public job-vacancy feed (arbeidsplassen.no).

Identity is exact: an ad is published for a company only when the live official feed entry names
the company's organisation number, or the number of one of its registered workplaces (official
subunit record), as the employer. The shipped index and the live feed catch-up only nominate ads.
Terms: https://arbeidsplassen.nav.no/vilkar-api (anyone may use and republish active ads).
"""
from __future__ import annotations
import gzip
import json
from pathlib import Path
import re
import threading
import urllib.parse
from .gate import contains_tokens, name_tokens, tokens

FEED = "https://pam-stilling-feed.nav.no"
FEED_ENTRY = FEED + "/api/v1/feedentry/"
TOKEN_URL = FEED + "/api/publicToken"
SHARED = "_shared"
MAX_ADS_PER_COMPANY = 12


def index_paths():
    here = Path(__file__).resolve().parent
    return [here / "data" / "nav-index.json.gz", here.parent / "data" / "nav-index.json.gz"]


def load_index():
    for path in index_paths():
        if path.is_file():
            try:
                data = json.loads(gzip.decompress(path.read_bytes()))
                data["path"] = str(path)
                return data
            except (OSError, ValueError, EOFError):
                continue
    return {"ads": {}, "by_org": {}, "built_at": None, "path": None}


class NavJobs:
    """One shared listing refresh per run; per-company matching afterwards."""

    def __init__(self, fetcher, *, live=True):
        self.fetcher = fetcher
        self.index = load_index()
        self.live_enabled = live
        self.live = {}
        self.token = None
        self.errors = []
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.listing_complete = False

    def homepages(self):
        """Employer homepages declared in indexed ads, by employer organisation number (a nomination
        for website discovery only; the identity gate still has to prove the site)."""
        found = {}
        for ad in self.index.get("ads", {}).values():
            url = str(ad.get("homepage") or "").strip()
            if ad.get("status") == "ACTIVE" and ad.get("orgnr") and url and "." in url:
                found.setdefault(str(ad["orgnr"]), [])
                if url not in found[str(ad["orgnr"])]:
                    found[str(ad["orgnr"])].append(url)
        return found

    def refresh(self, max_pages=400):
        """Catch up on the official feed from the index cursor: status changes and new ads."""
        try:
            r = self.fetcher.get(TOKEN_URL, SHARED)
            if r.state == "available":
                lines = [line.strip() for line in r.text().splitlines() if line.strip().count(".") == 2]
                self.token = lines[-1] if lines else None
            if not self.token:
                self.errors.append("public feed token unavailable: " + (r.error or f"HTTP {r.status}"))
                return
            cursor = self.index.get("cursor")
            if not self.live_enabled or not cursor:
                return
            url, pages = FEED + cursor, 0
            while url and pages < max_pages:
                page = self.fetcher.get(url, SHARED, headers={"Authorization": "Bearer " + self.token})
                body = page.json() if page.state == "available" else None
                if not isinstance(body, dict):
                    self.errors.append(f"feed page failed: {page.error or page.status}")
                    return
                with self.lock:
                    for item in body.get("items") or []:
                        entry = item.get("_feed_entry") or {}
                        uuid = entry.get("uuid") or item.get("id")
                        if uuid:
                            self.live[uuid] = {"status": entry.get("status"), "business_name": entry.get("businessName"),
                                               "title": entry.get("title")}
                pages += 1
                url = FEED + body["next_url"] if body.get("next_url") else None
            self.listing_complete = url is None
        except Exception as exc:  # noqa: BLE001 - recorded; jobs then come from the index only
            self.errors.append(f"refresh failed: {type(exc).__name__}: {str(exc)[:160]}")
        finally:
            self.ready.set()

    def candidates(self, org, unit_orgs, names):
        """Ad uuids nominated for this company (never evidence): indexed ads by employer number
        unless the feed has since closed them, plus new ads whose employer label names the company."""
        wanted = {org} | set(unit_orgs)
        picked = []
        with self.lock:
            live = dict(self.live)
        for number in wanted:
            for uuid in self.index.get("by_org", {}).get(number, []):
                if live.get(uuid, {}).get("status", "ACTIVE") == "ACTIVE":
                    picked.append(uuid)
        name_sets = [t for t in (name_tokens(n) for n in names) if t and (len(t) > 1 or len(t[0]) >= 4)]
        for uuid, ad in live.items():
            if ad.get("status") != "ACTIVE" or uuid in self.index.get("ads", {}):
                continue
            label = tokens(ad.get("business_name") or "")
            if any(contains_tokens(label, needle) for needle in name_sets):
                picked.append(uuid)
        return list(dict.fromkeys(picked))[:MAX_ADS_PER_COMPANY]


def closed_ads(nav, org, unit_orgs, names):
    """Ads of this employer that NAV's live feed now marks as not active (positive evidence of closure)."""
    wanted = {org} | set(unit_orgs)
    with nav.lock:
        live = dict(nav.live)
    closed = {uuid for number in wanted for uuid in nav.index.get("by_org", {}).get(number, [])
              if uuid in live and live[uuid].get("status") not in (None, "ACTIVE")}
    name_sets = [t for t in (name_tokens(n) for n in names) if t and (len(t) > 1 or len(t[0]) >= 4)]
    for uuid, ad in live.items():
        if ad.get("status") not in (None, "ACTIVE") and any(contains_tokens(tokens(ad.get("business_name") or ""), needle) for needle in name_sets):
            closed.add(uuid)
    return sorted(closed)


def research(profile, fetcher, nav, entity):
    """Publish active ads whose official feed entry names this company (or its workplace) as employer."""
    org = entity["organisasjonsnummer"]
    with profile.lock:
        units = {c["value"].get("organisation_number"): c["value"].get("name") for c in profile.data["claims"]
                 if c["field"] == "registered_workplace" and isinstance(c["value"], dict) and c["value"].get("organisation_number")}
    names = [entity.get("navn") or ""] + [name for name in units.values() if name]
    nominated = nav.candidates(org, set(units), names)
    attempt = {"strategy": "nav_job_feed_v1", "nominated": len(nominated), "index_built_at": nav.index.get("built_at"),
               "live_listing_complete": nav.listing_complete, "errors": nav.errors[:3],
               "confirmed_active": [], "confirmed_inactive": closed_ads(nav, org, set(units), names), "unverified": []}
    profile.data["attempts"].append(attempt)
    if not nominated:
        return 0
    if not nav.token:
        attempt["unverified"] = sorted(nominated)
        return 0
    published = 0
    agency = str((entity.get("naeringskode1") or {}).get("kode") or "").startswith("78.")
    for uuid in nominated:
        r = fetcher.get(FEED_ENTRY + uuid, org, headers={"Authorization": "Bearer " + nav.token})
        profile.snapshot(r)
        data = r.json() if r.state == "available" else None
        ad = (data or {}).get("ad_content") or {}
        employer = ad.get("employer") or {}
        employer_org = str(employer.get("orgnr") or "")
        if r.status in (404, 410) or (data and data.get("status") not in (None, "ACTIVE")):
            attempt["confirmed_inactive"] = sorted(set(attempt["confirmed_inactive"]) | {uuid})
            continue
        if not data or data.get("status") != "ACTIVE" or employer_org not in ({org} | set(units)) or not ad.get("title"):
            attempt["unverified"].append(uuid)  # failed fetch, or no longer this employer's: not proof of closure
            continue
        attempt["confirmed_active"].append(uuid)
        locations = ad.get("workLocations") or []
        first = locations[0] if locations and isinstance(locations[0], dict) else {}
        value = {"title": ad.get("title"), "job_title": ad.get("jobtitle"), "url": ad.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
                 "published": (ad.get("published") or "")[:10] or None, "expires": (ad.get("expires") or "")[:10] or None,
                 "application_due": ad.get("applicationDue"), "employment_type": ad.get("engagementtype"), "extent": ad.get("extent"),
                 "positions": ad.get("positioncount"), "location": ", ".join(filter(None, (first.get("city"), first.get("municipal")))) or None,
                 "employer_name": employer.get("name"), "employer_orgnr": employer_org,
                 "employer_relationship": "legal_entity" if employer_org == org else "registered_workplace_of_legal_entity",
                 "source": "NAV arbeidsplassen.no official job feed", "status": "active"}
        if agency:
            # NACE 78 (employment activities): the agency is the registered employer of record, but the
            # position is often with a client, so the ad must not read as the agency's own growth.
            value["posting_context"] = "employment_agency"
            value["note"] = "Posted by an employment or recruitment agency; the position may be with a client."
        proof = {"method": "employer_orgnr_in_official_job_feed", "organisation_number": org, "employer_orgnr": employer_org,
                 "workplace_of": org if employer_org != org else None, "publishable": True}
        scope = "legal_entity" if employer_org == org else "registered_workplace"
        claim = profile.add("job_posting", value, r, pointer="/ad_content/title", family="hiring", key=uuid,
                            effective_at=value["published"], source_class="public_job_feed", scope=scope, identity_proof=proof)
        if claim:
            profile.add("job_posting", value, r, pointer="/ad_content/employer/orgnr", family="hiring", key=uuid,
                        effective_at=value["published"], source_class="public_job_feed", scope=scope, identity_proof=proof)
            published += 1
    if published:
        profile.state("hiring", "available", f"{published} active job ads in NAV's official feed name this employer")
    return published
