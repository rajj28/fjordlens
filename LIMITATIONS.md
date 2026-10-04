# Known limits and honest abstention (FjordLens v0.2)

## What is not covered

- **No search engine**: Candidate URLs come only from registry fields (website, business e-mail domain), NAV employer homepages, and DNS-checked domains derived from the legal name or a registered workplace's trading name. No general web search, no Brave connector in the submitted runtime.
- **No JavaScript rendering**: Static HTML only. Dynamic content, SPA navigation, client-side hydration, and lazy-loaded data are not fetched.
- **No LinkedIn, Meta (Facebook/Instagram), Indeed, Glassdoor, Google, TikTok, X/Twitter scraping**: These hosts are hard-blocked in `net.RESTRICTED`. A company's outbound links to them may be recorded as `company_linked_profile` without fetching the destination.
- **PDF annual accounts not parsed**: Filing-history connector (`official.history`) returns download URLs and filing years only. Document extraction is not implemented.
- **Brønnøysund announcements excluded by robots.txt**: The `w2.brreg.no` announcements host disallows the agent via robots.txt; no announcements are collected.
- **TLS-broken sites skipped**: `website.fetch_page` tries www/non-www DNS fallback, then plain HTTP fallback. If all fail (certificate errors, handshake failures, connection refused, timeout, EOF), the site is marked `blocked`/`failed` and not researched.
- **No CAPTCHA solving, no authentication bypass, no paywall bypass**.
- **No browser automation / Playwright at runtime**: Playwright is an optional dev dependency only.
- **No sentiment analysis, review scoring, popularity ranking, or predicted official score**.
- **A careers page link does not prove current hiring**; a social link does not independently verify ownership of the destination account; a company publication is self-reported content.
- **Standard-library HTML extraction cannot recover every malformed or dynamic page**.
- **Request budgets can leave bounded research incomplete**, with explicit state/reason recorded in `availability`.
- **A live run cannot reconstruct evidence from a past cutoff**. Supply a trusted snapshot bundle (`--replay`) for historical evaluation. Timezone-aware comparisons reject prior evidence after the cutoff, but cannot independently authenticate a supplier's retrieval timestamps.
- **`--registry-snapshot` freezes only the official identity lookup**. Other connectors remain live; missing supplied identities cause abstention. Full offline reproduction requires `--replay`.
- **Historical replay and auditable refresh require raw snapshots beside the prior profile export**. Public profiles alone contain no complete raw-source bundle; missing required historical bytes are explicit failures. The raw evidence archive is kept private and excluded from version control.
- **The all-factor name/address/phone route (Rule D) is an auditable inference from agreeing sources**; confidence numbers are heuristic, not measured accuracy.
- **No JBOX bonus eligibility claimed**. The entry has no JBOX dependency.

## Source rights per source

| Source | Rights basis |
| --- | --- |
| Brønnøysund entity/roles/subunits/group APIs | NLOD 2.0 (Norwegian Licence for Open Government Data) |
| Regnskapsregisteret (annual accounts) | NLOD 2.0 |
| NAV arbeidsplassen.no job feed | Terms at https://arbeidsplassen.nav.no/vilkar-api (anyone may use and republish active ads) |
| Verified company websites | Public pages; robots.txt respected per RFC 9309. Robots is an access signal, not a licence grant. Operators must also honor applicable source terms and may disable a source when rights are unclear. |
| Name-derived domain guesses | DNS existence check, then a robots-respecting fetch of the homepage and up to two contact/about pages to run the identity gate; nothing is published unless the gate passes |

## Secrets

None. The runtime uses no API keys, tokens, or secrets. The optional Brave connector (not used in submission) would require an entrant-provided key passed at runtime.

## Safe URL handling

- `net.safe_url`: Validates scheme (http/https), standard ports (80/443), no credentials, no local/private hosts (localhost, .local, .internal, bare hostnames), no non-public IPs (RFC 1918, RFC 4193, link-local, multicast, reserved, 6to4/NAT64). Length ≤ 4096, no control chars, no backslashes.
- `net.resolve_public`: DNS lookup via `socket.getaddrinfo`; all returned addresses must be public unicast. Connection pins the first public IP (`PinnedHTTP`/`PinnedHTTPS`) while retaining original TLS SNI and Host header.
- Every redirect hop is revalidated through `safe_url`. Official endpoints cannot redirect outside `data.brreg.no`.
- Public-IP pinning prevents DNS rebinding and ensures the fetched host matches the validated address.