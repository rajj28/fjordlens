"""Build the declared NAV job-ad index from NAV's official public job-vacancy feed.

Starts the feed walk at a recent date (If-Modified-Since, default 120 days back), keeps the latest
status of every ad modified since then, and fetches the feed entry of each ACTIVE ad once for its
employer organisation number. Terms: https://arbeidsplassen.nav.no/vilkar-api (public token).
The result only NOMINATES ads: the agent re-fetches every published job live from the official feed
and re-checks the employer number. The saved cursor lets the agent read only newer feed pages.

    python scripts/build_nav_index.py            # writes fjordlens/data/nav-index.json.gz
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import gzip
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

FEED = "https://pam-stilling-feed.nav.no"
TOKEN_URL = FEED + "/api/publicToken"
UA = "FjordLens/0.2 (company research agent; NAV feed consumer)"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get(url, token, headers=None, attempts=6):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                                           "Authorization": "Bearer " + token, **(headers or {})})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == attempts - 1:
                raise
            time.sleep(min(float(exc.headers.get("retry-after") or 0) or 5 * (attempt + 1), 120))
        except Exception:  # noqa: BLE001 - transient network failure
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return None


def public_token():
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, headers={"User-Agent": UA}), timeout=60) as response:
        lines = response.read().decode("utf-8", "replace").splitlines()
    return next(line.strip() for line in reversed(lines) if line.strip().count(".") == 2)


def walk(token, days):
    """Latest feed state for every ad modified in the window, and the last page as cursor."""
    since = format_datetime(datetime.now(timezone.utc) - timedelta(days=days), usegmt=True)
    page = get(FEED + "/api/v1/feed", token, {"If-Modified-Since": since})
    ads, pages, cursor = {}, 0, None
    while page:
        for item in page.get("items") or []:
            entry = item.get("_feed_entry") or {}
            uuid = entry.get("uuid") or item.get("id")
            if uuid:
                ads[uuid] = {"status": entry.get("status"), "business_name": entry.get("businessName"),
                             "title": entry.get("title"), "municipal": entry.get("municipal"),
                             "modified": entry.get("sistEndret") or item.get("date_modified")}
        pages += 1
        cursor = page.get("feed_url") or cursor
        if pages % 25 == 0:
            last = ((page.get("items") or [{}])[-1]).get("date_modified", "")
            print(f"{now()} pages {pages} ads {len(ads)} at {last[:19]}", flush=True)
        if not page.get("next_url"):
            break
        page = get(FEED + page["next_url"], token)
        time.sleep(0.25)
    return ads, cursor, pages


def enrich(ads, token, workers):
    active = [u for u, ad in ads.items() if ad.get("status") == "ACTIVE"]
    print(f"{now()} fetching {len(active)} active feed entries", flush=True)

    def detail(uuid):
        entry = get(FEED + "/api/v1/feedentry/" + uuid, token) or {}
        ad = entry.get("ad_content") or {}
        employer = ad.get("employer") or {}
        return uuid, entry.get("status"), {"orgnr": employer.get("orgnr"), "employer_name": employer.get("name"),
                                           "homepage": employer.get("homepage") or None,
                                           "published": ad.get("published"), "expires": ad.get("expires")}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(detail, u) for u in active]):
            try:
                uuid, status, fields = future.result()
                ads[uuid].update(fields)
                if status:
                    ads[uuid]["status"] = status
            except Exception as exc:  # noqa: BLE001
                print("detail failed:", str(exc)[:100], flush=True)
            done += 1
            if done % 1000 == 0:
                print(f"{now()} details {done}/{len(active)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="fjordlens/data/nav-index.json.gz")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    token = public_token()
    started = now()
    ads, cursor, pages = walk(token, args.days)
    print(f"{now()} walked {pages} pages, {len(ads)} ads, cursor {cursor}", flush=True)
    enrich(ads, token, args.workers)
    active = {u: ad for u, ad in ads.items() if ad.get("status") == "ACTIVE"}
    by_org = {}
    for uuid, ad in active.items():
        if ad.get("orgnr"):
            by_org.setdefault(ad["orgnr"], []).append(uuid)
    out = {"schema": 3, "built_at": started, "window_days": args.days, "cursor": cursor, "source": FEED,
           "terms": "https://arbeidsplassen.nav.no/vilkar-api",
           "note": "Nomination index only; published jobs are re-fetched live from the official feed.",
           "ads": active, "by_org": by_org}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0))
    print(f"{now()} wrote {path}: {len(active)} active ads, {len(by_org)} employers", flush=True)


if __name__ == "__main__":
    main()
