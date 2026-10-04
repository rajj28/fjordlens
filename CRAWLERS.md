# Connectors and access policy (FjordLens v0.2)

| Connector | Published facts | Rights/access basis |
| --- | --- | --- |
| Brønnøysund entity API (`official.identity`) | Legal name, form, industry, addresses, dates, reported employees, status, registered activity, phone, email, website | Official open API, NLOD 2.0 |
| Official roles (`official.leadership`) | Active company-centric leaders and registered role holders | Official open roles API; no private-person lookup |
| Official subunits (`official.locations`) | Exact-parent registered workplaces | Official open API, NLOD 2.0 |
| Regnskapsregisteret (`official.financials`) | Filed amounts, currency, account type, reporting period, filing-year availability | Official public annual-accounts API |
| Group structure (`official.group`) | Direct parent/child relationships involving the target | Official open API |
| Verified company sites (`website.research`) | Descriptions, exact-company structured jobs, dated publications, site-declared social/contact links | Public pages, conservative robots handling; no authentication or paywall bypass |
| NAV job feed (`jobs.research`) | Active job ads where the official feed names the company (or its workplace) as employer | Official public feed (arbeidsplassen.no); terms at https://arbeidsplassen.nav.no/vilkar-api |

## Website discovery

`discovery.candidates` tries, in priority order:
1. Registry website (`hjemmeside` field)
2. Registry business-email domain (excludes known consumer/shared providers in `CONSUMER_EMAIL`)
3. Wikidata official website (from cached `caches["wikidata"]`)
4. NAV employer homepage (from cached `caches["nav_homepages"]`)
5. DNS guess from legal name (`discovery.guess_domains`, up to 6 domains, checked via live DNS when `fetcher.dns_enabled`)

Discovery does not establish ownership: every candidate still passes the exact legal-identity gate (`gate.assess`), and company subpages/feeds must remain on the verified host.

`--registry-snapshot <folder>` supplies frozen official identity responses for the exact `/enhetsregisteret/api/enheter/<org>` endpoint. The bundle uses the same metadata fields and layout as replay:
```
<folder>/snapshots/<content_sha256>.bin.gz
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
- Official endpoints: up to 2 retries on 5xx, 429 (respects `Retry-After`), and transient network errors.
- Other endpoints: 1 retry on same conditions.
- Retry waits: 1.5s × (attempt+1) for network errors; `Retry-After` or 2s for 429; 1.5s/4s for 5xx.
- No retries on budget errors, robots blocks, cutoff, replay misses, or policy violations.

### TLS/www fallbacks (`website.fetch_page`)
- On DNS failure: tries the other www/non-www host.
- On TLS/connection failure (no HTTP response at all): retries the same URL over plain HTTP.
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

## NAV feed index + runtime catch-up (`jobs.py`, `scripts/build_nav_index.py`)
- Shipped index: `fjordlens/data/nav-index.json.gz` (or parent `data/`) with `by_org` mapping from organisation number to ad UUIDs, and a `cursor` for live catch-up.
- `NavJobs.refresh` (run once per batch in background thread): fetches public token, then pages forward from the cursor using the bearer token, updating `self.live` with current statuses. Stops at `max_pages=400` or end of feed.
- `NavJobs.candidates` nominates ad UUIDs for a company: indexed ads by employer org number (unless feed has since closed them) plus new live ads whose employer label matches the company's name tokens.
- `jobs.research` fetches each nominated entry, verifies `status=ACTIVE` and employer org number matches the company or its registered workplaces, then publishes `job_posting` claims with `source_class="public_job_feed"` and `identity_proof.method="employer_orgnr_in_official_job_feed"`.