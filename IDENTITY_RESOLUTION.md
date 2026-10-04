# Exact legal identity — Gate v2 (FjordLens v1)

The identity gate (`gate.assess`) decides whether a fetched site is the exact legal entity's own site. Designed for zero wrong-company publications: one material mismatch blocks qualification.

## Evidence classes (E1–E6)

| Class | Description | Code location |
| --- | --- | --- |
| **E1** | Organisation number in an owner position (footer, labelled line, or site's own JSON-LD Organisation node) | `gate.assess` |
| **E2** | Full legal name in owner strings (title, h1, og:site_name, logo alt, copyright, JSON-LD Organization name/legalName) — with sister guard | `gate.assess`, `gate.owner_strings` |
| **E3** | Company-declared candidate (origin = `registry_website`, `registry_email_domain`, `nav_employer_homepage` (and `wikidata_official_website` when a cache is supplied)) | `gate.DECLARED_ORIGINS`, `gate.assess` |
| **E4** | Registry contact corroboration (phone, street+postcode, or email) — non-circular | `gate.assess` |
| **E5** | Conflicting owner organisation number (another legal entity's org number in owner position) | `gate.assess` |
| **E6** | Parked, directory, social, foreign .com guess (parked markers, NON_SITE_HOSTS, non-Norwegian .com without org owner) | `gate.assess` |

## Decision rules

| Rule | Condition | Code |
| --- | --- | --- |
| **A** | `org_owner` is true: our organisation number is in the site's owner position (homepage footer/structured/labels, or subpage with `name_owner`) | `gate.assess` |
| **B** | `declared` AND `name_owner` AND NOT `multi_entity`: company-declared site shows full legal name in owner strings | `gate.assess` |
| **D** | the site names itself as us (`name_owner`), or the exact legal name is in the text with fewer than two other legal names on the pages, AND the registered street with house number + postcode (`registry_address_on_site`) or the registered postcode written with its town (`registry_postcode_town_on_site`), NOT `multi_entity`, no longer legal name containing ours, no group words. Applies to every origin. Phone, e-mail or a registered person alone never accept a derived domain (Builderr evaluation report, 13 Sep). | `gate.assess` |
| **A2** | company's own declaration (registry website or e-mail domain, subunit website or e-mail domain, NAV employer homepage) AND our organisation number anywhere on the site AND no other organisation number AND our exact legal name AND a registry contact or registered person | `gate.assess` |
| **C** | registry-declared website (entity or subunit) AND our exact legal name on the site AND a registry contact or registered person AND no longer legal name containing ours, no group words in owner strings or domain, no group wording around our name (`gate.group_context`), NOT `multi_entity` | `gate.assess` |

Anything else → `decision="ambiguous"`, `publishable=False`.

## Key concepts

### Owner positions
Only these count as E1:
- Footer text (`page["footer"]`)
- Lines matching `ORG_LABEL` regex (organisation/VAT/MVA label)
- Site's own JSON-LD Organisation nodes at top level or publisher (`structured_org_ids` with `OWNER_NODE_PATH`)

Body text mentions, group subsidiary lists, article subjects, and nested organisations are **not** owner positions.

### Supplier credits (excluded from E1/E5)
`CREDIT` regex matches "website by", "design by", "developed by", "powered by", "hosted by", "webbyrå", "reklamebyrå", "leverandør av nettside", etc. Org numbers within ±120 chars of a credit match are excluded from `owner_mentions`.

### Multi-entity rule
If `all_numbers - {ctx.org}` is non-empty and `org_owner` is false → reject ("the site shows other legal entities' organisation numbers; only an organisation-number proof can accept it"). If `ctx.org` in `strong_all` AND other numbers in `strong_all` → reject ("several legal entities share the owner position (group or multi-company site)").

### Sister guard (part of E2)
`groupish = name_hit AND (tokens(name_hit) ∩ GROUPISH) - ctx.tokens`. If the owner string contains a group word (gruppe, group, konsern, holding) not in our name tokens, `name_owner` becomes false even if our tokens appear.

### Page-scope rule (`website.names_other_entity`)
If a subpage's title/h1/og:title names a legal entity (word + legal-form suffix) that is not exactly ours → the page is about that entity; none of its facts are attributed to us. Used in `website.extract`.

### Path-scoped sites
After gate passes, if the verified page URL has a non-trivial path section (not just language/home segments), site-wide facts are not attributed to this company. `website.research` sets all `SITE_FAMILIES` to `not_available` with reason "Verified page is a section of a larger site; site-wide facts are not attributed to this company".

### Social handle filter (`website.handle_matches_company`)
A site-declared social profile link is kept only if its handle carries the company or domain name: compact handle contains any distinctive legal-name token (len≥3), or the domain label, or the concatenated tokens. Excludes web agency/partner profiles linked from the same footer.

### Subunit declarations
Websites and e-mail domains registered on the company's own subunits (`underenheter`) are company declarations (origins `registry_workplace_website`, `registry_workplace_email_domain`); subunit phones, mobiles and e-mails are registry contacts for E4. An e-mail at the candidate's own domain is circular when the candidate came from that e-mail domain.

### Trading-name candidates
Domains derived from a registered workplace's trading name (origin `dns_guess_brand`) cannot carry the legal name, so only rule A (our organisation number in the site's owner position) can accept them.

### NAV employer orgnr rule (`jobs.research`)
A job ad is published only when the official feed entry's `employer.orgnr` matches the company's organisation number or one of its registered workplaces' organisation numbers. Name matching is never used as a fallback for job ads.

## Proof selection
On `publishable=True`, the gate records the strongest proof location:
1. Homepage JSON-LD structured org ID (`structured[ctx.org]`)
2. Homepage footer/labelled org number
3. Subpage footer/structured/labelled (if `name_owner`)
4. Owner string with legal name (`name_hit`)

Output: `proof_page_index`, `proof_selector` (parsed_html_pointer or normalized_text), `proof_span`, `proof_url`.