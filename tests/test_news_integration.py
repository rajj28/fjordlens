import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fjordlens import runner
from fjordlens.core import Profile
from fjordlens.observations import to_observations
from fjordlens.website import extract
from fjordlens.html import parse_html
from test_crawl_recall import ORG, ENTITY, SITE, response, proof
from test_observations_answers import claim, envelope


class NewsIntegrationTests(unittest.TestCase):
    def test_news_runs_after_website_connector_failure(self):
        pipeline = object.__new__(runner.Pipeline)
        pipeline.fetcher, pipeline.max_pages = object(), 12
        job = runner.Job(0, ORG, 'integration', None)
        job.entity = ENTITY
        with patch('fjordlens.runner.website.research', side_effect=RuntimeError('site down')), \
                patch('fjordlens.runner.news.research') as independent:
            pipeline.website_stage(job)
        independent.assert_called_once()
        self.assertIs(independent.call_args.args[2], ENTITY)

    def test_news_failure_preserves_existing_available_activity(self):
        pipeline = object.__new__(runner.Pipeline)
        pipeline.fetcher, pipeline.max_pages = object(), 12
        job = runner.Job(0, ORG, 'integration', None)
        job.entity = ENTITY
        job.profile.state('activity', 'available', 'Earlier supported publication')
        with patch('fjordlens.runner.website.research'), \
                patch('fjordlens.runner.news.research', side_effect=RuntimeError('news down')):
            pipeline.website_stage(job)
        self.assertEqual(job.profile.data['availability']['activity']['state'], 'available')
        self.assertEqual(job.profile.data['attempts'][-1]['state'], 'failed')

    def test_press_links_are_candidates_not_publications(self):
        p = Profile(ORG, 'press-links')
        r = response('<h1>Equinor in the press</h1><a href="https://publisher.no/new-factory">New factory opens in Norway</a>', 'press')
        extract(p, ENTITY, parse_html(r.text(), r.url), r, proof())
        self.assertEqual(p.news_candidates, ['https://publisher.no/new-factory'])
        self.assertFalse(any(c['field'] == 'company_publication' for c in p.data['claims']))

    def test_news_export_uses_supported_external_contract(self):
        env = envelope([claim('news', 'company_publication', 'activity', {
            'title': 'New factory', 'url': 'https://publisher.no/factory', 'published_on': '2026-10-17'
        }, source_class='third_party_publication')])
        row = to_observations(env)[0]
        self.assertEqual((row['platform'], row['signal_type']), ('news', 'public_mention'))
        self.assertEqual(row['source_class'], 'third_party_publication')
        self.assertEqual(row['acquisition_mode'], 'permitted_public_page')


if __name__ == '__main__':
    unittest.main()
