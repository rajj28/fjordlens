"""Exact-company section nomination must not relax root or third-party identity gates."""
import unittest
from unittest.mock import patch
from fjordlens import gate, website
from fjordlens.core import Profile
from fjordlens.html import parse_html
from fjordlens.net import Response
from fjordlens.sections import ORIGIN, assess_company_section, nominate_company_sections

ORG = "988573949"
OTHER = "923609016"
ROOT = "https://www.carlin.no/"
SECTION = ROOT + "index-cias.html"
ENTITY = {"organisasjonsnummer": ORG, "navn": "CARLIN INVEST AS", "hjemmeside": ROOT,
          "forretningsadresse": {"adresse": ["Vallerveien 152 E"], "postnummer": "1346", "poststed": "GJETTUM"}}
ROOT_HTML = '<title>Mats Carlin</title><p>My personal homepage. I also run my company.</p><a href="index-cias.html">Carlin Invest AS</a>'
OWN_HTML = f'<title>Carlin Invest AS</title><p>Investment and development.</p><footer>Carlin Invest AS. Org nr {ORG}</footer>'


def nominate(body=ROOT_HTML, origin="registry_website", declared=ROOT, fetched=ROOT, name=ENTITY["navn"], limit=2):
    return nominate_company_sections(name, {"url": declared, "origin": origin}, parse_html(body, fetched), body, limit=limit)


class SectionNominationTests(unittest.TestCase):
    def test_registry_origins_nominate_only_separate_section(self):
        for origin in gate.DECLARED_ORIGINS:
            with self.subTest(origin=origin):
                found = nominate(origin=origin)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["url"], SECTION)
                self.assertEqual(found[0]["origin"], ORIGIN)
                self.assertEqual(found[0]["parent_origin"], origin)
                self.assertNotIn("publishable", found[0])
                self.assertNotIn(ORIGIN, gate.DECLARED_ORIGINS)

    def test_search_guesses_and_recursive_section_discovery_are_forbidden(self):
        for origin in ("dns_guess", "search_name", "search_orgnr", ORIGIN, "unknown"):
            self.assertEqual(nominate(origin=origin), [])

    def test_source_must_be_declared_root_on_same_host(self):
        for declared, fetched in ((ROOT + 'profile', ROOT), (ROOT, ROOT + 'contact'),
                                  (ROOT, 'https://other.no/'), (ROOT, 'https://tenant.carlin.no/')):
            self.assertEqual(nominate(declared=declared, fetched=fetched), [])
        self.assertEqual(len(nominate(declared='http://carlin.no/', fetched=ROOT)), 1)

    def test_full_legal_name_only_with_case_and_whitespace_normalization(self):
        self.assertEqual(len(nominate('<a href="index-cias.html"> CARLIN <b>Invest</b> AS </a>')), 1)
        for label in ('Carlin Invest', 'Carlin Invest ASA', 'About Carlin Invest AS', 'Carlin Eiendom AS', 'Visit website'):
            self.assertEqual(nominate(f'<a href="index-cias.html">{label}</a>'), [])
        self.assertEqual(nominate('<a href="company.html">BOR AS</a>', name='B\u00d8R AS'), [])

    def test_image_alt_or_hidden_text_does_not_nominate(self):
        for body in ('<a href="index-cias.html"><img alt="Carlin Invest AS"></a>',
                     '<div hidden><a href="index-cias.html">Carlin Invest AS</a></div>',
                     '<a style="display: none" href="index-cias.html">Carlin Invest AS</a>',
                     '<div aria-hidden="true"><a href="index-cias.html">Carlin Invest AS</a></div>',
                     '<script><a href="index-cias.html">Carlin Invest AS</a></script>'):
            self.assertEqual(nominate(body), [])

    def test_no_customer_supplier_credit_or_article_references(self):
        link = '<a href="index-cias.html">Carlin Invest AS</a>'
        for body in ('<div class="customers">' + link + '</div>', '<h2>Our clients</h2>' + link,
                     '<p>Website by ' + link + '</p>', '<article>' + link + '</article>',
                     '<p>Supplier: ' + link + '</p>'):
            self.assertEqual(nominate(body), [], body)
        for target in ('/customers/carlin', '/referanser/carlin', '/supplier/carlin', '/case/carlin'):
            self.assertEqual(nominate(f'<a href="{target}">Carlin Invest AS</a>'), [])

    def test_no_cross_host_queries_fragments_credentials_or_assets(self):
        for target in ('https://other.no/company', 'https://customer.carlin.no/company', '/?company=carlin',
                       '/index-cias.html#company', '/index-cias.html?tracking=1', '/company.pdf',
                       'https://user:secret@carlin.no/company', 'javascript:alert(1)', '/'):
            self.assertEqual(nominate(f'<a href="{target}">Carlin Invest AS</a>'), [], target)

    def test_dedup_and_hard_max_two_candidates(self):
        body = ''.join(f'<a href="{url}">Carlin Invest AS</a>' for url in ('/one/', '/one', '/two/', '/three/'))
        self.assertEqual([c['url'] for c in nominate(body, limit=100)], [ROOT + 'one/', ROOT + 'two/'])
        self.assertEqual(nominate(body, limit=0), [])


class SectionProofTests(unittest.TestCase):
    def setUp(self):
        self.ctx = gate.Context(ENTITY)
        self.candidate = nominate()[0]

    def assess(self, body, url=SECTION, candidate=None):
        return assess_company_section(self.ctx, candidate or self.candidate, parse_html(body, url))

    def test_name_only_section_cannot_use_declared_root_trust(self):
        result = self.assess('<title>Carlin Invest AS</title><p>Investment and consulting services to businesses.</p>')
        self.assertFalse(result['publishable'])
        trusted = {**self.candidate, 'origin': 'registry_website'}
        self.assertFalse(self.assess(OWN_HTML, candidate=trusted)['publishable'])

    def test_own_organisation_number_accepts_section_alone(self):
        result = self.assess(OWN_HTML)
        self.assertTrue(result['publishable'], result)
        self.assertTrue(result['rule'].startswith('A:'))
        self.assertEqual(result['proof_page_index'], 0)
        self.assertEqual(result['candidate_url'], SECTION)
        self.assertEqual(result['proof_url'], SECTION)

    def test_own_name_and_registered_address_allow_rule_d(self):
        body = '<title>Carlin Invest AS</title><p>We provide investment and development services to businesses in Norway. Vallerveien 152 E, 1346 Gjettum.</p>'
        result = self.assess(body)
        self.assertTrue(result['publishable'], result)
        self.assertTrue(result['rule'].startswith('D:'))

    def test_body_subject_and_contact_are_not_section_ownership(self):
        body = '<title>Mats Carlin</title><p>Carlin Invest AS is located at Vallerveien 152 E, 1346 Gjettum.</p>'
        self.assertFalse(self.assess(body)['publishable'])

    def test_wrong_entity_and_customer_case_rejected(self):
        wrong = f'<title>Other Company AS</title><footer>Other Company AS Org nr {OTHER}</footer>'
        self.assertFalse(self.assess(wrong)['publishable'])
        reference = f'<title>Customer case: Carlin Invest AS</title><footer>Carlin Invest AS Org nr {ORG}</footer>'
        self.assertFalse(self.assess(reference)['publishable'])

    def test_redirect_to_root_sibling_or_other_host_rejected_even_with_number(self):
        for url in (ROOT, ROOT + 'some-other-section.html', 'https://other.no/index-cias.html'):
            self.assertFalse(self.assess(OWN_HTML, url)['publishable'])


class SectionIntegrationTests(unittest.TestCase):
    def run_research(self, section_html):
        profile = Profile(ORG, 'section-test')
        profile.data['legal_identity'] = {'organisation_number': ORG, 'name': ENTITY['navn'], 'source_snapshot_id': 'registry-test'}
        bodies = {ROOT: ROOT_HTML + '<a href="https://facebook.com/root-owner">Facebook</a>', SECTION: section_html}
        class FakeFetcher:
            dns_enabled = False
            def get(self, url, *args, **kwargs):
                return Response(url, url, 200 if url in bodies else 404, bodies.get(url, '').encode(), {'content-type': 'text/html'}, '2026-10-17T00:00:00Z')
        with patch.object(website.discovery, 'candidates', return_value=([{'url': ROOT, 'origin': 'registry_website'}], [])), \
             patch.object(website.search, 'enabled', return_value=False), \
             patch.object(website, 'crawl_site') as crawl, patch.object(website, 'probe_missing') as probe, \
             patch.object(website, 'wordpress_posts') as posts:
            website.research(profile, FakeFetcher(), ENTITY)
            crawl.assert_not_called()
            probe.assert_not_called()
            posts.assert_not_called()
        return profile.data

    def test_research_publishes_section_url_and_no_root_or_sitewide_claims(self):
        result = self.run_research(OWN_HTML)
        self.assertEqual([(c['field'], c['value']) for c in result['claims']], [('official_website', SECTION)])
        self.assertEqual(result['evidence'][0]['source_url'], SECTION)
        self.assertEqual(result['evidence'][0]['identity_proof']['url'], SECTION)
        self.assertEqual(result['availability']['social_profiles']['state'], 'not_available')
        self.assertIn('section', result['availability']['social_profiles']['reason'])
        self.assertFalse(result['identity_assessments'][0]['publishable'])
        self.assertTrue(result['identity_assessments'][-1]['publishable'])

    def test_wrong_section_does_not_publish_website_or_root_claims(self):
        result = self.run_research(f'<title>Other AS</title><footer>Other AS Org nr {OTHER}</footer>')
        self.assertEqual(result['claims'], [])
        self.assertFalse(any(a['publishable'] for a in result['identity_assessments']))


if __name__ == '__main__':
    unittest.main()
