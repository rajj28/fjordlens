"""CMS metadata and feeds must not turn static utility pages into news."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fjordlens.core import Profile
from fjordlens.html import parse_html
from fjordlens.net import Budget, Fetcher
from fjordlens.website import extract, feed, wordpress_posts
from scripts.audit_evidence import audit
from test_crawl_recall import ENTITY, ORG, SITE, Fetcher as FakeFetcher, proof, publications, response


class UtilityPublicationTests(unittest.TestCase):
    def test_jsonld_news_types_cannot_publish_utility_pages(self):
        for path in ('kontakt-oss/category29.html', 'personvern/', 'om-oss/article-1956',
                     'contact/category331.html', 'privacy-policy/', 'services/design'):
            for kind in ('Article', 'NewsArticle', 'BlogPosting'):
                with self.subTest(path=path, kind=kind):
                    html = '<h1>Company information</h1><script type="application/ld+json">' + json.dumps({
                        '@type': kind, 'headline': 'Company information', 'datePublished': '2020-01-01'
                    }) + '</script>'
                    captured = response(html, path)
                    profile = Profile(ORG, 'utility')
                    extract(profile, ENTITY, parse_html(html, captured.url), captured, proof())
                    self.assertEqual(publications(profile), [])

    def test_feed_keeps_editorial_privacy_story_and_drops_utility_page(self):
        xml = '<rss><channel>' + ''.join(
            '<item><title>Our privacy update</title><link>' + SITE + path +
            '</link><pubDate>2026-10-01</pubDate></item>'
            for path in ('privacy-policy/', 'news/privacy-update')) + '</channel></rss>'
        profile = Profile(ORG, 'feed')
        feed(profile, ENTITY, response(xml, 'feed', 'application/xml'), proof())
        self.assertEqual([c['value']['url'] for c in publications(profile)], [SITE + 'news/privacy-update'])

    def test_wordpress_posts_cannot_bypass_utility_filter(self):
        api = SITE + 'wp-json/wp/v2/posts?per_page=10&_fields=id,date,link,title'
        posts = [{'id': i, 'date': '2026-10-01', 'link': SITE + path,
                  'title': {'rendered': 'Our privacy update'}}
                 for i, path in enumerate(('privacy-policy/', 'news/privacy-update'))]
        profile = Profile(ORG, 'wordpress')
        wordpress_posts(profile, FakeFetcher({api: response(json.dumps(posts), api, 'application/json')}),
                        ORG, response('wp-json'), proof())
        self.assertEqual([c['value']['url'] for c in publications(profile)], [SITE + 'news/privacy-update'])

    def test_audit_rejects_legacy_utility_publication_even_with_valid_selector(self):
        html = '<h1>Company information</h1><script type="application/ld+json">' + json.dumps({
            '@type': 'Article', 'headline': 'Company information', 'datePublished': '2020-01-01'
        }) + '</script>'
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            captured = response(html, 'contact/')
            Fetcher(folder, Budget())._save(captured)
            profile = Profile(ORG, 'legacy')
            # Reproduce the earlier extractor accepting generic Article schema on a contact page.
            with patch('fjordlens.articles.nonpublication_url', return_value=False):
                extract(profile, ENTITY, parse_html(html, captured.url), captured, proof())
            self.assertEqual(len(publications(profile)), 1)
            (folder / 'envelopes.jsonl').write_text(json.dumps(profile.data) + '\n', encoding='utf-8')
            result = audit(folder)
            self.assertFalse(result['passed'])
            self.assertTrue(any('Utility page' in e['error'] for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
