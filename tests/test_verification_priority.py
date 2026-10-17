import unittest

from fjordlens import gate
from fjordlens.core import Profile
from fjordlens.html import parse_html
from fjordlens.website import try_candidates
from test_crawl_recall import ORG, SITE, ENTITY, OWNER, Fetcher, response


class VerificationPriorityTests(unittest.TestCase):
    def test_nested_contact_link_reaches_owner_proof(self):
        home = response('<html lang="nb"><title>Equinor ASA</title><p>Vi tilbyr tjenester til bedrifter i Norge og arbeider med utvikling av energiprosjekter.</p><a href="/about">About</a>')
        about = response('<h1>Equinor ASA</h1><a href="/legal/company">Legal notice</a>', 'about')
        legal = response(OWNER, 'legal/company')
        fetcher = Fetcher({SITE: home, about.url: about, legal.url: legal})
        accepted, _ = try_candidates(Profile(ORG, 'nested-proof'), fetcher, ORG, gate.Context(ENTITY),
                                    [{'url': SITE, 'origin': 'dns_guess'}], 'not_available')
        self.assertIsNotNone(accepted)
        self.assertEqual(fetcher.calls, [SITE, about.url, legal.url])

    def test_conventional_proof_is_tried_after_unhelpful_link(self):
        home = response('<html lang="nb"><title>Equinor ASA</title><p>Vi tilbyr tjenester til bedrifter i Norge og arbeider med utvikling av energiprosjekter.</p><a href="/about">About</a>')
        about = response('<h1>Equinor ASA</h1>', 'about')
        contact = response(OWNER, 'kontakt')
        fetcher = Fetcher({SITE: home, about.url: about, contact.url: contact})
        accepted, _ = try_candidates(Profile(ORG, 'fallback-proof'), fetcher, ORG, gate.Context(ENTITY),
                                    [{'url': SITE, 'origin': 'dns_guess'}], 'not_available')
        self.assertIsNotNone(accepted)
        self.assertIn(contact.url, fetcher.calls)

    def test_frontier_is_bounded_and_does_not_follow_foreign_proof(self):
        home = response('<html lang="nb"><title>Equinor ASA</title><p>Vi tilbyr tjenester til bedrifter i Norge og arbeider med utvikling av energiprosjekter.</p><a href="/contact/0">Contact</a>')
        pages = {SITE: home}
        for n in range(10):
            page = response(f'<h1>Equinor ASA</h1><a href="/contact/{n + 1}">Contact</a>'
                            '<a href="https://other.no/legal">Legal notice</a>', f'contact/{n}')
            pages[page.url] = page
        fetcher = Fetcher(pages)
        accepted, _ = try_candidates(Profile(ORG, 'bounded-proof'), fetcher, ORG, gate.Context(ENTITY),
                                    [{'url': SITE, 'origin': 'dns_guess'}], 'not_available')
        self.assertIsNone(accepted)
        self.assertEqual(len(fetcher.calls), 6)
        self.assertTrue(all(url.startswith(SITE) for url in fetcher.calls))

    def test_multiple_search_proof_pages_are_consumed_without_duplicates(self):
        home = response('<html lang="nb"><title>Equinor ASA</title><p>Vi tilbyr tjenester til bedrifter i Norge og arbeider med utvikling av energiprosjekter.</p>')
        legal = response(OWNER, 'legal/owner')
        fetcher = Fetcher({SITE: home, legal.url: legal})
        first = SITE + 'contact/old'
        accepted, _ = try_candidates(Profile(ORG, 'deep-proofs'), fetcher, ORG, gate.Context(ENTITY),
                                    [{'url': SITE, 'origin': 'search_name', 'verification_url': first,
                                      'verification_urls': [first, legal.url]}], 'not_available')
        self.assertIsNotNone(accepted)
        self.assertEqual(fetcher.calls, [SITE, first, legal.url])

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
