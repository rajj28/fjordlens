import unittest

from fjordlens import gate
from fjordlens.core import Profile
from fjordlens.html import parse_html
from fjordlens.website import try_candidates
from test_crawl_recall import ORG, SITE, ENTITY, OWNER, Fetcher, response


class VerificationPriorityTests(unittest.TestCase):
    def test_legal_owner_notice_is_found_before_form_terms_and_about(self):
        links = ''.join(f'<a href="{path}">{title}</a>' for path, title in (
            ('/pages/contact', 'Contact'), ('/pages/terms', 'Terms'),
            ('/about', 'About'), ('/policies/contact-information', 'Contact information'),
            ('/policies/legal-notice', 'Legal notice'), ('/personvern', 'Personvern')))
        home = response('<html lang="nb"><title>Equinor ASA</title><p>We provide energy and offshore services for customers across Norway.</p>' + links + '</html>')
        legal = response(OWNER, 'policies/legal-notice')
        fetcher = Fetcher({SITE: home, legal.url: legal})
        accepted, _ = try_candidates(Profile(ORG, 'proof-priority'), fetcher, ORG, gate.Context(ENTITY),
                                    [{'url': SITE, 'origin': 'dns_guess'}], 'not_available')
        self.assertIsNotNone(accepted)
        self.assertEqual(fetcher.calls, [SITE, legal.url])
        self.assertIn(ORG, accepted[0]['proof_span'])

    def test_legal_notice_with_different_owner_still_fails(self):
        home = response('<html lang="nb"><title>Equinor ASA</title><p>We provide energy and offshore services for customers across Norway.</p><a href="/legal-notice">Legal notice</a></html>')
        legal = response('<footer>Other Supplier AS Org nr 914778271</footer>', 'legal-notice')
        fetcher = Fetcher({SITE: home, legal.url: legal})
        accepted, _ = try_candidates(Profile(ORG, 'proof-priority'), fetcher, ORG, gate.Context(ENTITY),
                                    [{'url': SITE, 'origin': 'dns_guess'}], 'not_available')
        self.assertIsNone(accepted)


if __name__ == '__main__':
    unittest.main()
