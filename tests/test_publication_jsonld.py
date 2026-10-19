"""Publication subjects and exported fields must agree with their captured source."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from fjordlens.core import Profile
from fjordlens.html import parse_html
from fjordlens.net import Response
from fjordlens.website import extract, wordpress_posts
from scripts.audit_evidence import audit

ORG = '923609016'
SITE = 'https://example.no/'
DAY = '2026-10-17T12:00:00Z'
ENTITY = {'organisasjonsnummer': ORG, 'navn': 'EXAMPLE AS'}
PROOF = {'organisation_number': ORG, 'publishable': True, 'url': SITE, 'method': 'identity_gate_v2', 'rule': 'test owner'}


def captured(body, url=SITE + 'news/current', content_type='text/html'):
    r = Response(url, url, 200, body.encode('utf-8'), {'content-type': content_type}, DAY)
    r.storage_path = 'snapshots/' + r.sha256 + ('.json' if 'json' in content_type else '.html')
    return r


def publication(profile):
    return next(c for c in profile.data['claims'] if c['field'] == 'company_publication')


class FakeFetcher:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


class PublicationSubjectTests(unittest.TestCase):
    def structured(self, node, title='Current company announcement'):
        html = '<h1>' + title + '</h1><script type="application/ld+json">' + json.dumps(node) + '</script>'
        r, profile = captured(html), Profile(ORG, 'jsonld-test')
        extract(profile, ENTITY, parse_html(html, r.url), r, PROOF)
        return profile, r

    def test_main_entity_and_identifier_publish_their_actual_article_url(self):
        for extra in ({'mainEntityOfPage': {'@id': SITE + 'news/different'}},
                      {'@id': SITE + 'news/different#article'},
                      {'url': '/news/different'}, {'mainEntityOfPage': {'@id': '../news/different'}}):
            with self.subTest(extra=extra):
                p, _ = self.structured({'@type': 'NewsArticle', 'headline': 'A different company story',
                                        'datePublished': '2026-10-14T11:00:00+02:00', **extra})
                c = publication(p)
                self.assertEqual(c['value']['url'], SITE + 'news/different')
                self.assertEqual(c['value']['title'], 'A different company story')
                self.assertEqual(c['value']['published_at'], '2026-10-14T11:00:00+02:00')

    def test_url_less_mismatching_headline_is_not_current_page(self):
        p, _ = self.structured({'@type': 'NewsArticle', 'headline': 'An unrelated story', 'datePublished': '2026-10-14'})
        self.assertFalse(any(c['field'] == 'company_publication' for c in p.data['claims']))

    def test_url_less_matching_headline_can_bind_to_current_page(self):
        p, _ = self.structured({'@type': 'NewsArticle', 'headline': 'Current company announcement', 'datePublished': '2026-10-14'})
        self.assertEqual(publication(p)['value']['url'], SITE + 'news/current')

    def test_offsite_or_invalid_explicit_subject_does_not_default_to_current_page(self):
        for subject in ('https://different.no/story', 'javascript:alert(1)', 'urn:article:123'):
            with self.subTest(subject=subject):
                p, _ = self.structured({'@type': 'NewsArticle', 'headline': 'Current company announcement',
                                        'datePublished': '2026-10-14', 'url': subject})
                self.assertFalse(any(c['field'] == 'company_publication' for c in p.data['claims']))


class PublicationAuditTests(unittest.TestCase):
    structured = PublicationSubjectTests.structured

    def check_audit(self, profile, response, mutation=None):
        # Audit just the publication, avoiding unrelated company-description assertions.
        data = copy.deepcopy(profile.data)
        data['claims'] = [c for c in data['claims'] if c['field'] == 'company_publication']
        if mutation:
            mutation(data['claims'][0])
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'snapshots').mkdir()
            (folder / response.storage_path).write_bytes(response.body)
            (folder / 'envelopes.jsonl').write_text(json.dumps(data) + '\n', encoding='utf-8')
            return audit(folder)

    def test_jsonld_audit_rejects_altered_title_url_date_and_timestamp(self):
        p, r = self.structured({'@type': 'NewsArticle', 'headline': 'A different company story',
                                'datePublished': '2026-10-14T11:00:00+02:00', 'mainEntityOfPage': SITE + 'news/different'})
        self.assertTrue(self.check_audit(p, r)['passed'])
        for field, bad in (('title', 'Invented title'), ('url', SITE + 'news/current'),
                           ('published_on', '2026-10-15'), ('published_at', '2026-10-15T00:00:00Z')):
            with self.subTest(field=field):
                result = self.check_audit(p, r, lambda c: c['value'].__setitem__(field, bad))
                self.assertFalse(result['passed'])
                self.assertTrue(any('differs from saved source' in e['error'] for e in result['errors']))

    def test_wordpress_audit_compares_post_fields_instead_of_only_pointer_existence(self):
        post = {'id': 9, 'title': {'rendered': 'A real &amp; useful company update'},
                'link': SITE + 'news/update', 'date': '2026-10-14T11:00:00'}
        r = captured(json.dumps([post]), SITE + 'wp-json/wp/v2/posts?per_page=10', 'application/json')
        p = Profile(ORG, 'wp-audit')
        wordpress_posts(p, FakeFetcher(r), ORG, captured('wp-json', SITE), PROOF)
        self.assertTrue(self.check_audit(p, r)['passed'])
        for field, bad in (('title', 'Invented title'), ('url', SITE + 'news/different'),
                           ('published_on', '2026-10-15'), ('published_at', '2026-10-15T00:00:00Z')):
            with self.subTest(field=field):
                self.assertFalse(self.check_audit(p, r, lambda c: c['value'].__setitem__(field, bad))['passed'])

    def test_page_article_audit_rejects_altered_title_and_timestamp(self):
        html = ('<meta property="og:type" content="article"><meta property="article:published_time" '
                'content="2026-10-14T11:00:00Z"><h1>Current company announcement</h1>')
        p, r = Profile(ORG, 'page-audit'), captured(html)
        extract(p, ENTITY, parse_html(html, r.url), r, PROOF)
        self.assertTrue(self.check_audit(p, r)['passed'])
        for field, bad in (('title', 'Invented title'), ('published_at', '2026-10-15T00:00:00Z')):
            with self.subTest(field=field):
                self.assertFalse(self.check_audit(p, r, lambda c: c['value'].__setitem__(field, bad))['passed'])

    def test_listing_audit_rejects_altered_title_or_invented_timestamp(self):
        html = '<li><a href="/news/update">A useful company update</a><time datetime="2026-10-14">14 October 2026</time></li>'
        p, r = Profile(ORG, 'listing-audit'), captured(html, SITE)
        extract(p, ENTITY, parse_html(html, r.url), r, PROOF)
        self.assertTrue(self.check_audit(p, r)['passed'])
        for field, bad in (('title', 'Invented title'), ('published_at', '2026-10-14T00:00:00Z')):
            with self.subTest(field=field):
                self.assertFalse(self.check_audit(p, r, lambda c: c['value'].__setitem__(field, bad))['passed'])

    def test_forged_utility_target_is_rejected_before_selector_dispatch(self):
        p, r = self.structured({'@type': 'NewsArticle', 'headline': 'A different company story',
                                'datePublished': '2026-10-14', 'url': SITE + 'news/different'})
        result = self.check_audit(p, r, lambda c: c['value'].__setitem__('url', SITE + 'contact'))
        self.assertFalse(result['passed'])
        self.assertTrue(any('Utility page' in e['error'] for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
