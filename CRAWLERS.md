# Connectors and access policy (FjordLens v1.2)

| Connector | Published facts | Rights/access basis |
| --- | --- | --- |
| Brønnøysund entity API (`official.identity`) | Legal name, form, industry, addresses, dates, reported employees, status, registered activity, phone, email, website | Official open API, NLOD 2.0 |
| Official roles (`official.leadership`) | Active company-centric leaders and registered role holders | Official open roles API; no private-person lookup |
| Official subunits (`official.locations`) | Exact-parent registered workplaces | Official open API, NLOD 2.0 |
| Regnskapsregisteret (`official.financials`) | Filed amounts, currency, account type, reporting period, filing-year availability | Official public annual-accounts API |
| Group structure (`official.group`) | Direct parent/child relationships involving the target | Official open API |
| Verified company sites (`website.research`) | Descriptions, exact-company structured jobs, dated publications, site-declared social/contact links | Public pages, conservative robots handling; no authentication or paywall bypass |
| Optional web search (`search.nomination_batches`) | Nothing directly: nominates candidates for identity and article evidence checks | Brave Web Search API with an evaluator-supplied key only; results used transiently, never stored |
| Independent news (`news.research`) | Article title, direct URL, publication date and publisher; exact company bound in the article body | Licensed search or verified-site press links nominate public HTML; ordinary robots/TLS/budget checks apply |
| NAV job feed (`jobs.research`) | Active job ads where the official feed names the company (or its workplace) as employer | Official public feed (arbeidsplassen.no); terms at https://arbeidsplassen.nav.no/vilkar-api |

## Website discovery

`discovery.candidates` tries, in priority order:
1. Registry website (`hjemmeside` field)
2. Registry business-email domain (excludes known consumer/shared providers in `CONSUMER_EMAIL`)
3. Websites and business-email domains registered on the company's own subunits
4. Wikidata official website (from cached `caches["wikidata"]`, when supplied)
5. NAV employer homepage (from cached `caches["nav_homepages"]`)
6. DNS guess from legal name (`discovery.guess_domains`, up to 6 domains, checked via live DNS when `fetcher.dns_enabled`),
   and from registered workplace trading names (these can only pass by organisation number). Legal-name hints include
   descriptor-stripped names, alternate Norwegian spellings, IDNA names and AS suffixes within the same six-domain
   cap. Removing a descriptor affects nomination only; the full legal identity is still checked by the gate.
7. Only when none of the free candidates passes the gate and an evaluator-supplied `BRAVE_SEARCH_API_KEY` (or
   `BRAVE_API_KEY`) is set: `search.nomination_batches` queries the organisation number
   (`"923609016" OR "923 609 016"`), then `"legal name" town` if no first-batch candidate is verified.
   The second query is allowed even when the first found candidates that the gate rejected. Registered-workplace
   brand and organisation-number/legal-proof queries can follow, up to four total. Each query nominates at most
   three roots; a verified candidate stops iteration. Brand/acronym domains need not spell the legal name, but still
   face the full identity gate. Directories and social platforms are excluded. Up to three `verification_urls`
   preserve deep legal/contact results on a domain. Name-search nominations pass like DNS guesses (rule A or D);
   organisation-number nominations only by rule A, because a supplier's or customer's page also shows our number.

### Verification and bounded traversal

- Up to five free candidates are tried before optional search. Each homepage is checked before extra proof pages
  are requested. Legal notices, imprint pages and contact-information pages take priority, followed by contact,
  privacy, general terms and about pages; intent groups are interleaved to avoid exhausting the limit on variants
  of one page. A homepage with an owner-name match permits up to five proof pages; otherwise the cap is three.
  Same-host deep search results are tried first. Contact pages can nominate nested legal/contact pages within the
  same three/five-request cap. Conventional paths are tried after linked pages are exhausted only when the owner
  name matches and proof is missing. Any hard rejection stops that candidate's verification.
- A verified section of a larger site is published as that section's URL. Site-wide descriptions, jobs, profiles
  and publications are withheld for a section so they are not attributed to the subsidiary or tenant.
- `--max-pages` defaults to 12. `crawl_site` counts the accepted homepage and its fetched verification pages
  against this content-page allowance, then expands newly fetched pages into a bounded frontier. Contact, hiring,
  activity and about routes share the allowance; news-listing links can lead to individual article pages.
- `sitemap_links` reads at most four sitemap documents, including nested indexes, preferring post/news maps.
  It starts at `/sitemap.xml` and can fall back to `/sitemap_index.xml`. Sitemaps are consulted when the initial
  frontier has no activity route, or when the frontier empties. `lastmod` only orders candidate fetches; it is
  never used as a publication date.
- Frontier URLs are deduplicated, and utility routes and binary assets are skipped. The content-page allowance
  is not a total HTTP cap: robots, redirects, rejected candidates, sitemap documents, missing-family probes,
  WordPress API calls and up to two eligible feeds also consume the shared run/per-company request budgets.

An unverified registry-declared root may nominate up to two same-host company-section links whose visible
text exactly names the legal entity. Hidden and customer/supplier/reference links are excluded. Each section
must pass its own A/D owner proof without registry-origin shortcuts or root-page evidence. A verified section
publishes only its section URL; facts from the surrounding site are not attributed to the company.

### Extraction on a verified site (`website.extract`, one fact per source key; the first extractor wins)
- Descriptions (meta description on home/about pages, first substantive paragraph), public brand (`og:site_name`
  or the site's own Organization/WebSite markup).
- Contacts: e-mail at the verified domain, the registry phone found on the site, and phone numbers that the site's
  own contact page labels with a cue word (`Tlf`, `Telefon`, `Mobil`, `Phone`, ...; 8-digit Norwegian numbers,
  at most three) unless another company or a third-party role (accountant, auditor, landlord, partner, supplier,
  web agency, ...) is named in the same or the two preceding lines; the quote is the number exactly as written in
  the saved HTML. Organization markup with our organisation number.
- Social profiles: links whose handle carries the company or domain name, and `sameAs` URLs in the site's own
  Organization node when that node carries our organisation number or exactly our legal name and no other
  identifier (same handle rule); destinations are never fetched.
- Publications: RSS/Atom items, Article/NewsArticle/BlogPosting markup, dated links on news/blog listing pages and the
  homepage (`articles.extract_dated_links`: links on the verified host only; a time element or written date inside
  the link or in the nearest enclosing element that links to no other page; the quote is the verbatim source covering
  link and date, and an item whose link and date cannot be quoted together is skipped), an article page's own date
  (`article:published_time` meta, a JSON-LD article node about that page, or a supported publication time on the
  page's single `<article>`; pagination pages are listings, not articles), and up to 10 posts from the site's WordPress REST API
  (`/wp-json/wp/v2/posts`, only when the homepage references `wp-json`; the API and each post must be on the verified
  host; WordPress's default "Hello world!" post is ignored). Article dates before 1990 and future publication
  dates are rejected. A generic creation-date meta tag plus an `<article>` wrapper does not establish that a
  root-level services page is a publication. CMS static-page/archive markers require stronger article evidence.
  JSON-LD must identify the fetched page, including functional query parameters; tracking parameters and equivalent
  Unicode/percent-encoded paths do not create duplicate articles. Encoded slashes remain distinct.
- Publication-date safeguards: modification labels such as `Updated`, `modified`, `oppdatert` and `dateModified`
  do not become publication dates. Atom `updated` alone is insufficient. Comment/review feeds, their channel
  titles, and item links into comment anchors are excluded. Product/catalog/collection URLs and Shopify
  product feed entries are excluded from publications. Feed evidence stays tied to the captured feed bytes;
  its article link is a separate content URL, not a claim that the article itself was fetched. `published_on`
  retains the source's calendar date. When meta, JSON-LD, WordPress, feed or article-time evidence includes a time,
  `published_at` preserves the original ISO or RFC timestamp; a missing timezone is not invented or converted.
  Visible HTML times require `itemprop=datePublished` or a local publication label such as Published/Publisert.
  Hidden, comment, footer, sidebar and event times cannot supply publication dates. URL-less Article markup
  with a headline that differs from the page's headline cannot lend its date to that page.
  Contact, about, privacy, legal and service utility routes are excluded across HTML, structured metadata,
  feeds and WordPress. Booking/contact call-to-action labels cannot become article titles merely because
  a date appears nearby. Editorial paths such as `/news/privacy-update` remain eligible.
- Missing-type probe: when the link-driven crawl leaves publications or contacts empty, up to two conventional pages
  per missing family (`/nyheter`, `/aktuelt`, `/blogg`, `/news`; `/kontakt`, `/kontakt-oss`, `/contact`).
- Hiring: JobPosting markup on the verified site (expired `validThrough` skipped); careers pages are recorded as part
  of the website, never as a hiring fact.

Discovery does not establish ownership: every candidate still passes the exact legal-identity gate (`gate.assess`), and company subpages/feeds must remain on the verified host.

`--registry-snapshot <folder>` supplies frozen official identity responses for the exact `/enhetsregisteret/api/enheter/<org>` endpoint. The bundle uses the same metadata fields and layout as replay:
```
<folder>/snapshots/<content_sha256>.<ext>   (raw bytes; legacy .bin.gz accepted)
<folder>/snapshots/ss_<id>.json
```
These identity reads verify body hashes, retain original retrieval times, and consume zero new HTTP attempts. A missing supplied identity remains unavailable; it is not replaced with a live fetch. Financials, roles, workplaces, group, filing history, and website connectors remain live under normal policies and budgets. `--replay` supersedes this option and serves all sources from its bundle without network access.

Official API reference: https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html

## Network layer (`net.py`)

### Budget and pacing
- `Budget` (`net.Budget`): run-level `max_requests` (default ~30×companies, min 2000), wall-clock `seconds` (default 2600), per-company `per_company` (default 40). Shared sources (e.g., NAV feed catch-up) use `org="_shared"` and count only toward the run total.
- Deadline margin: requests stop 5–50% before the hard limit to allow finalization.
- Separate rate lanes per host: `official_interval` (default 0.1s, env `FJORDLENS_OFFICIAL_INTERVAL`), `site_interval` (default 0.5s, env `FJORDLENS_SITE_INTERVAL`). Filing-copy endpoint (`/aarsregnskap/kopi/`) gets its own lane at 1.1s.
- `Robots` crawl-delay increases the effective interval when present (capped at 30s; longer delays skip the site).

### Robots.txt (RFC 9309)
- `Fetcher._robots_allowed` fetches `/robots.txt` with up to 5 redirect hops (every hop revalidated as a public URL by `safe_url`).
- Status handling: 2xx → parse; 4xx (except 429) → "unavailable" (allow all); 429/5xx/network/TLS failure → "unreachable" (disallow all).
- Group selection: exact user-agent token prefix match, then `*` groups. Longest-match precedence with `$` anchor; `Allow` beats `Disallow` at equal length.
- `Robots.from_response` implements the full policy in `robots.py`.

### Retries

- Robots resolution gets one retry across its entire redirect chain for transient timeout/429/5xx failures.
  Temporary robots and ordinary URL failures expire after 30 seconds on later demand, extended by Retry-After.
  Explicit denials, TLS/policy failures and replay outcomes do not become allowed through retries. HTTP-date
  Retry-After values are supported; waits that exceed the deadline or permitted delay are skipped.
- Official endpoints: up to 2 retries on 5xx, 429 (respects `Retry-After`), and transient network errors.
- Other endpoints: 1 retry on same conditions.
- Retry waits: 1.5s × (attempt+1) for network errors; `Retry-After` (max 20 s) or 2s for 429; 1.5s/4s for 5xx.
  A company site answering 429 with `Retry-After` above 5 s is not retried.
- Every request has a hard total deadline of twice the socket timeout (never past the run deadline): a watchdog
  shuts the socket down, so a server that hangs or trickles header/body bytes cannot hold a worker
  (`tests/test_network_stress.py`).
- No retries on budget errors, robots blocks, cutoff, replay misses, or policy violations.

### TLS/www fallbacks (`website.fetch_page`)
- On DNS, certificate or transient TLS/connection failure with no HTTP response: tries the other www/non-www host.
- A failed HTTPS request can also try the same URL over plain HTTP. At most two alternate URLs are attempted;
  each still passes normal robots, public-IP and request-budget checks.
- HTTP error statuses (4xx, 5xx) are answers and not retried here.

### Restricted platforms
Hard-blocked on every request (including robots.txt redirects): `linkedin.com`, `facebook.com`, `instagram.com`, `glassdoor.com`, `indeed.com`, `google.com`, `tiktok.com`, `x.com`, `twitter.com`.

### Request budget and deadline behaviour
- `Budget.reserve` refuses when: wall-clock deadline passed, run `max_requests` reached, per-company limit reached, or declared external API spend would exceed $9.
- `Fetcher.get` checks `time.monotonic() + timeout > budget.deadline` before dialing.
- `Budget.close` (called at finalization) moves the deadline to `now`, refusing all further requests.

### Safe URL handling and public-IP pinning
- `net.safe_url`: validates scheme (http/https), standard ports (80/443), no credentials, no local/private hosts, no non-public IPs.
- `net.resolve_public`: DNS lookup; all returned addresses must be public unicast (not multicast, reserved, link-local, 6to4/NAT64). Connection pins the first public IP while retaining original TLS SNI and Host header.
- Every redirect hop is revalidated through `safe_url`. Official endpoints cannot redirect outside `data.brreg.no`.

## Independent news and coverage diagnostics

`news.research` runs after website research even when no company site was verified. It consumes up to three
article candidates from a verified site's outbound press links, or nominates them with at most two additional
Brave Web queries. All search and page requests share the company/run/cost budgets. Search snippets, titles and
dates never become evidence. No JavaScript, paywall or restricted-platform bypass is used.

An accepted article needs a same-page publication date and headline, one visible article/main body, the target
company in the headline, and its exact legal name in a local body block corroborated by its labeled organisation
number, explicit registered address, or a link to an already identity-verified company root. Registry e-mail or
unverified homepage domains cannot alone corroborate news identity. Navigation, footer, comments, related stories,
modification dates and mismatched article metadata cannot supply proof. Title/date/identity spans are retained
against fetched bytes and re-derived by `audit_evidence.py` using the saved official identity response.

Records use `source_class=third_party_publication`, exported as `platform=news, signal_type=public_mention`.
Answers and summaries label them third-party reports; no sentiment or independent editorial quality is inferred.
The run report separates company coverage by source/family, publication records from publication companies, and
website outcomes. These diagnostics are not official recall scores.

## NAV feed index + runtime catch-up (`jobs.py`, `scripts/build_nav_index.py`)
- Shipped index: `fjordlens/data/nav-index.json.gz` (or parent `data/`) with `by_org` mapping from organisation number to ad UUIDs, and a `cursor` for live catch-up.
- `NavJobs.refresh` (run once per batch in background thread): fetches public token, then pages forward from the cursor using the bearer token, updating `self.live` with current statuses. Stops at `max_pages=400` or end of feed.
- `NavJobs.candidates` nominates ad UUIDs for a company: indexed ads by employer org number (unless feed has since closed them) plus new live ads whose employer label matches the company's name tokens.
- `jobs.research` fetches each nominated entry, verifies `status=ACTIVE` and employer org number matches the company or its registered workplaces, then publishes `job_posting` claims with `source_class="public_job_feed"` and `identity_proof.method="employer_orgnr_in_official_job_feed"`.
