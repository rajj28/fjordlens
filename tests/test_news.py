"""Independent news must survive same-name, publisher-footer and date-binding traps."""
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from fjordlens import news
from fjordlens.core import Profile, validate
from fjordlens.net import Response

ORG = "923609016"
OTHER_ORG = "988573949"
ENTITY = {"organisasjonsnummer": ORG, "navn": "NORD EKSEMPEL AS", "hjemmeside": "https://nordeksempel.no/",
          "forretningsadresse": {"adresse": ["Testveien 12"], "postnummer": "0123", "poststed": "OSLO"}}
URL = "https://lokalavisen.no/nyheter/nord-eksempel-vinner-avtale"
TODAY = "2026-10-17"
IDENTITY = '<p>Nord Eksempel AS, org.nr. 923 609 016, har inngått en ny avtale.</p>'


def article(body=IDENTITY, *, title="Nord Eksempel vinner en ny avtale", date="2026-10-15T23:30:00-04:00", extra=""):
    return ('<html><head><meta property="og:type" content="article">'
            f'<meta property="article:published_time" content="{date}"><title>{title}</title>{extra}'
            f'</head><body><article><h1>{title}</h1>{body}</article></body></html>')


def response(html, url=URL, **kwargs):
    result = Response(url, url, 200, html.encode("utf-8"), {"content-type": "text/html; charset=utf-8"},
                      TODAY + "T12:00:00Z", **kwargs)
    result.storage_path = "snapshots/" + result.sha256 + ".html"
    return result


class FakeFetcher:
    def __init__(self, pages=None, search_results=None):
        self.pages, self.search_results, self.calls = pages or {}, search_results or [], []

    def get(self, url, org, **kwargs):
        self.calls.append((url, org, kwargs))
        if url.startswith(news.search.ENDPOINT):
            results = self.search_results.pop(0) if self.search_results else []
            return Response(url, url, 200, json.dumps({"web": {"results": results}}).encode(),
                            {"content-type": "application/json"}, TODAY + "T12:00:00Z")
        found = self.pages.get(url, Response(url, url, 404, retrieved_at=TODAY + "T12:00:00Z"))
        return replace(found, source_class=kwargs.get("source_class", found.source_class))


class ArticleTests(unittest.TestCase):
    def extract(self, html=None, url=URL, entity=ENTITY, domains=()):
        return news.extract_article(html if html is not None else article(), url, entity, TODAY, domains)

    def test_exact_entity_body_has_literal_identity_title_date_spans(self):
        html = article()
        found = self.extract(html)
        self.assertIsNotNone(found)
        self.assertEqual(found["value"]["published_on"], "2026-10-15")
        self.assertEqual(found["value"]["published_at"], "2026-10-15T23:30:00-04:00")
        self.assertEqual(found["value"]["source_kind"], "third_party_publication")
        self.assertEqual(found["value"]["url"], URL)
        self.assertEqual(found["identity_proof"]["rule"], "legal_name_and_labeled_org_in_article")
        for span in found["spans"].values():
            self.assertIn(span, html)

    def test_same_name_company_with_other_number_is_rejected(self):
        self.assertIsNone(self.extract(article(IDENTITY.replace("923 609 016", OTHER_ORG))))
        self.assertIsNone(self.extract(article('<p>Nord Eksempel AS has a new agreement.</p>')))

    def test_publisher_footer_or_sidebar_cannot_supply_target_number(self):
        for tag in ('footer', 'aside', 'nav'):
            body = f'<p>Nord Eksempel AS has a new agreement.</p><{tag}>{IDENTITY}</{tag}>'
            self.assertIsNone(self.extract(article(body)))
        self.assertIsNone(self.extract(article('<p>Nord Eksempel AS has a new agreement.</p>') + '<footer>' + IDENTITY + '</footer>'))

    def test_hidden_comment_related_and_review_text_cannot_bind(self):
        for attr in ('class="comments"', 'id="related-posts"', 'class="review"', 'hidden', 'aria-hidden="true"', 'style="display: none"'):
            self.assertIsNone(self.extract(article(f'<div {attr}>{IDENTITY}</div>')))

    def test_article_header_byline_and_related_cards_cannot_supply_identity(self):
        for wrapper in ('<header>{}</header>', '<div role="complementary">{}</div>',
                        '<div class="relatedPosts">{}</div>', '<div class="author-bio">{}</div>',
                        '<div class="publisher">{}</div>'):
            with self.subTest(wrapper=wrapper):
                self.assertIsNone(self.extract(article(wrapper.format(IDENTITY))))

    def test_name_and_number_in_unrelated_paragraphs_are_not_joined(self):
        self.assertIsNone(self.extract(article('<p>Nord Eksempel AS has a new agreement.</p><p>Publisher org.nr. 923609016.</p>')))
        self.assertIsNone(self.extract(article('<p>Nord Eksempel AS has a new agreement. Publisher org.nr. 923609016.</p>')))

    def test_bare_number_and_conflicting_entity_numbers_are_rejected(self):
        self.assertIsNone(self.extract(article(IDENTITY.replace("org.nr.", "reference"))))
        self.assertIsNone(self.extract(article(IDENTITY.replace('</p>', f' Other company org.nr. {OTHER_ORG}.</p>'))))

    def test_incidental_mention_without_company_headline_is_rejected(self):
        self.assertIsNone(self.extract(article(title="An unrelated publisher announces its results")))

    def test_own_company_site_does_not_become_third_party_news(self):
        self.assertIsNone(self.extract(url="https://nordeksempel.no/news/new-contract"))
        self.assertIsNone(self.extract(domains={"lokalavisen.no"}))

    def test_company_domain_link_must_name_company_in_same_paragraph(self):
        body = '<p><a href="https://nordeksempel.no/">Nord Eksempel AS</a> has a new agreement.</p>'
        found = self.extract(article(body), domains={"nordeksempel.no"})
        self.assertEqual(found["identity_proof"]["rule"], "legal_name_and_company_domain_link_in_article")
        self.assertIsNone(self.extract(article(body.replace("nordeksempel.no", "different-company.no"))))
        self.assertIsNone(self.extract(article('<p>Nord Eksempel AS has a new agreement. <a href="https://nordeksempel.no/">Publisher</a></p>')))

    def test_registry_accountant_or_parent_domain_does_not_prove_news_identity(self):
        for declarations in ({"hjemmeside": "https://accountant.no/"}, {"hjemmeside": "", "epostadresse": "customer@accountant.no"}):
            entity = {**ENTITY, **declarations}
            body = '<p><a href="https://accountant.no/">Nord Eksempel AS</a> has a new agreement.</p>'
            self.assertIsNone(self.extract(article(body), entity=entity))
        self.assertIsNone(self.extract(article('<p><a href="https://nordeksempel.no/">Nord Eksempel AS</a> has a new agreement.</p>')))

    def test_registry_address_must_be_explicit_company_address_block(self):
        body = '<address>Nord Eksempel AS, Testveien 12, 0123 Oslo</address>'
        self.assertEqual(self.extract(article(body))["identity_proof"]["rule"], "legal_name_and_registry_address_in_article")
        self.assertIsNone(self.extract(article(body.replace("0123", "0456"))))
        self.assertIsNone(self.extract(article(body.replace("address", "p"))))

    def test_modified_date_future_date_and_unlabelled_generic_date_rejected(self):
        self.assertIsNone(self.extract(article().replace("article:published_time", "article:modified_time")))
        self.assertIsNone(self.extract(article(date="2027-01-01")))
        self.assertIsNone(self.extract(article().replace('<meta property="og:type" content="article">', '').replace('article:published_time', 'date')))
        body = article().replace('<meta property="article:published_time" content="2026-10-15T23:30:00-04:00">', '')
        self.assertIsNone(self.extract(body.replace(IDENTITY, '<time datetime="2026-10-15">Updated 15 October 2026</time>' + IDENTITY)))

    def test_comment_hidden_and_meeting_times_are_not_publication_dates(self):
        undated = article().replace('<meta property="article:published_time" content="2026-10-15T23:30:00-04:00">', '')
        cases = {
            'comment': '<section class="comments"><p>Published <time datetime="2026-10-15">15 October 2026</time></p></section>',
            'hidden_parent': '<div hidden>Published <time datetime="2026-10-15">15 October 2026</time></div>',
            'hidden_time': '<time hidden itemprop="datePublished" datetime="2026-10-15">15 October 2026</time>',
            'meeting': '<p>Meeting scheduled for <time datetime="2026-10-15">15 October 2026</time>.</p>',
            'unlabelled': '<time datetime="2026-10-15">15 October 2026</time>',
            'sidebar': '<aside>Published <time datetime="2026-10-15">15 October 2026</time></aside>',
        }
        for name, markup in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(self.extract(undated.replace(IDENTITY, markup + IDENTITY)))

    def test_visible_explicit_publication_time_remains_valid(self):
        undated = article().replace('<meta property="article:published_time" content="2026-10-15T23:30:00-04:00">', '')
        for markup in ('<p>Published <time datetime="2026-10-15T12:00:00Z">15 October 2026</time></p>',
                       '<time itemprop="datePublished" datetime="2026-10-15T12:00:00Z">15 October 2026</time>'):
            with self.subTest(markup=markup):
                found = self.extract(undated.replace(IDENTITY, markup + IDENTITY))
                self.assertIsNotNone(found)
                self.assertEqual(found['value']['published_on'], '2026-10-15')

    def test_news_listing_catalog_homepage_and_multiple_article_cards_rejected(self):
        for url in ('https://lokalavisen.no/', 'https://lokalavisen.no/news', 'https://lokalavisen.no/products/new-product'):
            self.assertIsNone(self.extract(url=url))
        self.assertIsNone(self.extract(article().replace('</body>', '<article>' + IDENTITY + '</article></body>')))

    def test_other_page_canonical_and_schema_do_not_bind(self):
        self.assertIsNone(self.extract(article(extra='<link rel="canonical" href="https://lokalavisen.no/another-story">')))
        self.assertIsNone(self.extract(article(extra='<script type="application/ld+json">{"@type":"NewsArticle","url":"https://lokalavisen.no/another-story","datePublished":"2026-10-15"}</script>')))

    def test_functional_query_preserved_and_tracking_not_identity(self):
        url = URL + '?story=123&utm_source=email'
        found = self.extract(url=url)
        self.assertEqual(found['value']['url'], url)
        self.assertIsNone(self.extract(article(extra=f'<link rel="canonical" href="{URL}?story=456">'), url=url))

    def test_main_article_with_page_metadata_supported(self):
        found = self.extract(article().replace('<article>', '<main>').replace('</article>', '</main>'))
        self.assertIsNotNone(found)

    def test_jsonld_publication_date_bound_to_same_page(self):
        html = article().replace('<meta property="article:published_time" content="2026-10-15T23:30:00-04:00">', '')
        html = html.replace('</head>', '<script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "url": URL, "datePublished": "2026-10-14"}) + '</script></head>')
        self.assertEqual(self.extract(html)["value"]["published_on"], "2026-10-14")

    def test_url_less_schema_for_another_headline_is_not_this_article_date(self):
        html = article().replace('<meta property="article:published_time" content="2026-10-15T23:30:00-04:00">', '')
        html = html.replace('</head>', '<script type="application/ld+json">' + json.dumps({
            "@type": "NewsArticle", "headline": "Another company buys a competitor", "datePublished": "2026-10-13"
        }) + '</script></head>')
        self.assertIsNone(self.extract(html))


class ConnectorTests(unittest.TestCase):
    def profile(self):
        return Profile(ORG, "news-test")

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': '', 'BRAVE_API_KEY': ''})
    def test_inactive_without_key_makes_no_requests_and_preserves_activity(self):
        profile, fetcher = self.profile(), FakeFetcher()
        profile.state("activity", "available", "Existing owned publication")
        self.assertEqual(news.research(profile, fetcher, ENTITY), 0)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(profile.data["availability"]["activity"]["state"], "available")

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': '', 'BRAVE_API_KEY': ''})
    def test_keyless_nominated_article_has_three_replayable_evidence_refs(self):
        profile = self.profile()
        fetcher = FakeFetcher({URL: response(article())})
        self.assertEqual(news.research(profile, fetcher, ENTITY, nominated_urls=[URL]), 1)
        self.assertEqual(len(profile.data["claims"]), 1)
        claim = profile.data["claims"][0]
        self.assertEqual(claim["source_class"], "third_party_publication")
        self.assertEqual(len(claim["evidence_ids"]), 3)
        self.assertEqual(claim['claim_span'], IDENTITY)
        self.assertEqual(profile.data['evidence'][0]['selector']['part'], 'identity')
        self.assertEqual(validate(profile.data), [])
        self.assertEqual(fetcher.calls[0][2]["source_class"], "third_party_publication")
        for evidence in profile.data["evidence"]:
            selector = evidence["selector"]
            found = news.extract_article(article(), URL, selector["entity"], TODAY, selector["company_domains"])
            self.assertEqual(claim["value"], found["value"])
            self.assertEqual(evidence["claim_span"], found["spans"][selector["part"]])

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': 'fake', 'BRAVE_API_KEY': ''})
    def test_search_snippet_cannot_become_evidence_and_candidate_budget_is_global(self):
        urls = [URL + str(n) for n in range(5)]
        results = [{"url": u, "title": "Nord Eksempel AS org.nr. 923609016", "description": IDENTITY, "age": "2026-10-15"} for u in urls]
        fetcher = FakeFetcher({u: response('<p>Not an article.</p>', u) for u in urls}, [results, results])
        profile = self.profile()
        self.assertEqual(news.research(profile, fetcher, ENTITY, max_candidates=999), 0)
        self.assertEqual(len(profile.data["claims"]), 0)
        self.assertEqual(len(fetcher.calls), 4)  # one query, three article fetches
        self.assertEqual(fetcher.calls[0][2]["store"], False)
        self.assertEqual(profile.data["attempts"][-1]["fetched"], 3)
        self.assertNotIn("description", str(profile.data["attempts"]))

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': 'fake', 'BRAVE_API_KEY': ''})
    def test_empty_first_query_uses_name_query_and_works_without_website(self):
        fetcher = FakeFetcher({URL: response(article())}, [[], [{"url": URL}]])
        profile = self.profile()
        profile.state("website", "ambiguous", "No own site verified")
        self.assertEqual(news.research(profile, fetcher, ENTITY), 1)
        self.assertEqual(profile.data["attempts"][-1]["queries"], 2)
        self.assertEqual(profile.data["availability"]["website"]["state"], "ambiguous")

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': '', 'BRAVE_API_KEY': ''})
    def test_duplicates_and_redirects_to_own_site_do_not_publish(self):
        redirected = response(article())
        redirected.url = 'https://nordeksempel.no/news/story'
        fetcher, profile = FakeFetcher({URL: redirected}), self.profile()
        self.assertEqual(news.research(profile, fetcher, ENTITY, nominated_urls=[URL, URL + '#fragment']), 0)
        self.assertEqual(len(fetcher.calls), 1)

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': '', 'BRAVE_API_KEY': ''})
    def test_blocked_article_and_restricted_candidate_do_not_change_existing_state(self):
        fetcher = FakeFetcher({URL: Response(URL, URL, error="Robots: disallowed")})
        profile = self.profile()
        profile.state('activity', 'available', 'Own site already has a dated article')
        self.assertEqual(news.research(profile, fetcher, ENTITY, nominated_urls=['https://linkedin.com/posts/example', URL]), 0)
        self.assertEqual(profile.data['availability']['activity']['state'], 'available')
        self.assertEqual(len(fetcher.calls), 1)

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': '', 'BRAVE_API_KEY': ''})
    def test_invalid_initial_nominations_do_not_hide_valid_fourth_article(self):
        fetcher, profile = FakeFetcher({URL: response(article())}), self.profile()
        nominations = ['https://linkedin.com/posts/example', 'https://lokalavisen.no/',
                       'https://nordeksempel.no/news/story', URL]
        self.assertEqual(news.research(profile, fetcher, ENTITY, nominated_urls=nominations), 1)
        self.assertEqual(len(fetcher.calls), 1)

    def test_audit_domains_use_current_verified_claims_and_registry_only(self):
        envelope = {'claims': [
            {'field': 'official_website', 'value': 'https://verified.no/', 'availability': 'available', 'scope': 'legal_entity', 'stale': False},
            {'field': 'official_website', 'value': 'https://stale.no/', 'availability': 'available', 'scope': 'legal_entity', 'stale': True},
            {'field': 'official_website', 'value': 'https://parent.no/tenant', 'availability': 'available', 'scope': 'legal_entity', 'stale': False},
        ]}
        self.assertEqual(news.company_domains(envelope, ENTITY), {'verified.no'})

    @patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': '', 'BRAVE_API_KEY': ''})
    def test_saved_evidence_audit_rederives_article_and_rejects_forged_context(self):
        from scripts.audit_evidence import audit

        profile = self.profile()
        registry_url = 'https://data.brreg.no/enhetsregisteret/api/enheter/' + ORG
        registry = Response(registry_url, registry_url, 200, json.dumps(ENTITY).encode(),
                            {'content-type': 'application/json'}, TODAY + 'T12:00:00Z', source_class='official_registry')
        registry.storage_path = 'snapshots/' + registry.sha256 + '.json'
        profile.snapshot(registry)
        profile.data['legal_identity'] = {'name': ENTITY['navn'], 'organisation_number': ORG, 'source_snapshot_id': registry.snapshot_id}
        captured = response(article())
        self.assertEqual(news.research(profile, FakeFetcher({URL: captured}), ENTITY, nominated_urls=[URL]), 1)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'snapshots').mkdir()
            for saved in (registry, captured):
                (folder / saved.storage_path).write_bytes(saved.body)
            path = folder / 'envelopes.jsonl'
            path.write_text(json.dumps(profile.data) + '\n', encoding='utf-8')
            result = audit(folder)
            self.assertTrue(result['passed'], result['errors'])
            profile.data['evidence'][0]['selector']['entity']['navn'] = 'A different same-name target'
            path.write_text(json.dumps(profile.data) + '\n', encoding='utf-8')
            result = audit(folder)
            self.assertFalse(result['passed'])
            self.assertTrue(any('registry snapshot' in e['error'] for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
