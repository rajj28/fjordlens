import unittest

from test_observations_answers import claim, envelope
from fjordlens.observations import to_observations


class ObservationExportTests(unittest.TestCase):
    def test_registry_declared_profile_keeps_registry_acquisition(self):
        env = envelope([claim('social', 'company_profile', 'social_profiles', {
            'platform': 'Facebook', 'url': 'https://www.facebook.com/example',
            'destination_verified': False,
        }, source_class='official_registry', scope='registry_declared_link')])
        row = to_observations(env)[0]
        self.assertEqual(row['acquisition_mode'], 'official_api')
        self.assertEqual(row['platform'], 'facebook')
        self.assertFalse(row['value']['destination_verified'])
        self.assertEqual(row['scope'], 'registry_declared_link')

    def test_article_url_does_not_replace_captured_feed_url(self):
        env = envelope([claim('article', 'company_publication', 'activity', {
            'title': 'A new factory', 'url': 'https://x.no/factory', 'published_on': '2026-10-01'
        }, source_url='https://x.no/feed', effective_at='2026-10-01')])
        row = to_observations(env)[0]
        self.assertEqual(row['source_url'], 'https://x.no/feed')
        self.assertEqual(row['content_url'], 'https://x.no/factory')
        self.assertEqual(row['published_on'], '2026-10-01')
        self.assertEqual(row['evidence_ids'], ['ev_article'])

    def test_workplace_count_is_not_exported_as_parent_headcount(self):
        env = envelope([claim('staff', 'workplace_employees', 'workforce', 0,
                              source_class='official_subunits', scope='registered_subunit')])
        self.assertEqual(to_observations(env), [])

    def test_stale_profiles_are_not_exported_as_current(self):
        env = envelope([claim('social', 'company_profile', 'social_profiles', {
            'platform': 'Facebook', 'url': 'https://www.facebook.com/example'
        }, stale=True)])
        self.assertEqual(to_observations(env), [])


if __name__ == '__main__':
    unittest.main()
