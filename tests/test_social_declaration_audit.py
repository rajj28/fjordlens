"""Social declarations must retain their exact declaring organisation and target."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from fjordlens.core import Profile
from fjordlens.html import parse_html
from fjordlens.net import Response
from fjordlens.website import extract, own_organisation_node, social_profile_url
from scripts.audit_evidence import audit


ORG = '923609016'
SITE = 'https://example.no/'
LINK = 'https://www.linkedin.com/company/86803995'
ENTITY = {'organisasjonsnummer': ORG, 'navn': 'EXAMPLE AS'}
PROOF = {'organisation_number': ORG, 'publishable': True, 'url': SITE,
         'method': 'identity_gate_v2', 'rule': 'test owner'}


class SocialDeclarationAuditTests(unittest.TestCase):
    def test_nonweb_credentials_and_invalid_ports_never_become_social_profiles(self):
        for url in ('javascript://www.linkedin.com/company/123',
                    'ftp://www.linkedin.com/company/123',
                    '//www.linkedin.com/company/123',
                    'https://someone@www.linkedin.com/company/123',
                    'https://www.linkedin.com:invalid/company/123',
                    'https://www.linkedin.com:80/company/123',
                    'https://www.linkedin.com:444/company/123',
                    'https://www.linked\nin.com/company/123'):
            with self.subTest(url=url):
                self.assertIsNone(social_profile_url(url))
        self.assertIsNotNone(social_profile_url(LINK))
        self.assertIsNotNone(social_profile_url(LINK.replace('https:', 'http:')))

    def extract_nodes(self, nodes):
        body = '<script type="application/ld+json">' + json.dumps(nodes) + '</script>'
        response = Response(SITE, SITE, 200, body.encode(), {'content-type': 'text/html'},
                            '2026-10-20T00:00:00Z')
        response.storage_path = 'snapshots/' + response.sha256 + '.html'
        page = parse_html(body, SITE)
        profile = Profile(ORG, 'social-declaration-audit')
        profile.data['legal_identity']['name'] = ENTITY['navn']
        extract(profile, ENTITY, page, response, PROOF)
        profile.data['claims'] = [c for c in profile.data['claims'] if c['field'] == 'company_linked_profile']
        return profile.data, response, page

    def test_same_name_different_id_duns_sibling_cannot_supply_sameas(self):
        nodes = [
            {'@type': 'Organization', '@id': SITE + '#verified', 'name': ENTITY['navn'], 'taxID': ORG},
            {'@type': 'Organization', '@id': 'https://other.example/#entity', 'name': ENTITY['navn'],
             'identifier': {'@type': 'PropertyValue', 'propertyID': 'DUNS', 'value': '123456789'},
             'sameAs': [LINK]},
        ]
        data, response, _ = self.extract_nodes(nodes)
        self.assertEqual(data['claims'], [])
        self.assertTrue(own_organisation_node(parse_html(
            '<script type="application/ld+json">' + json.dumps(nodes) + '</script>', SITE),
            nodes[1], '/0/1', ORG, ENTITY['navn']) is False)

    def test_singleton_name_only_numeric_profile_is_allowed_and_audits(self):
        nodes = [{'@type': 'Organization', 'name': ENTITY['navn'], 'sameAs': [LINK]}]
        data, response, _ = self.extract_nodes(nodes)
        self.assertEqual(len(data['claims']), 1)
        self.assertTrue(self.check(data, response)['passed'])

    def test_same_id_alias_with_consistent_org_and_url_is_allowed(self):
        shared_id = SITE + '#company'
        nodes = [
            {'@type': 'Organization', '@id': shared_id, 'name': ENTITY['navn'], 'taxID': ORG, 'url': SITE},
            {'@type': 'Organization', '@id': shared_id, 'name': ENTITY['navn'], 'url': SITE, 'sameAs': [LINK]},
        ]
        data, response, _ = self.extract_nodes(nodes)
        self.assertEqual(len(data['claims']), 1)
        self.assertTrue(self.check(data, response)['passed'])

    def test_cross_site_node_url_is_rejected(self):
        nodes = [{'@type': 'Organization', 'name': ENTITY['navn'], 'taxID': ORG,
                  'url': 'https://other.example/', 'sameAs': [LINK]}]
        data, response, page = self.extract_nodes(nodes)
        self.assertEqual(data['claims'], [])
        self.assertFalse(own_organisation_node(page, nodes[0], '/0/0', ORG, ENTITY['navn']))

    def fixture(self, scalar=False):
        nodes = [
            {'@type': 'Organization', 'name': ENTITY['navn'], 'taxID': ORG,
             'sameAs': LINK if scalar else [LINK]},
            {'@type': 'Organization', 'name': 'OTHER AS',
             'sameAs': [LINK]},
        ]
        body = '<script type="application/ld+json">' + json.dumps(nodes) + '</script>'
        response = Response(SITE, SITE, 200, body.encode(), {'content-type': 'text/html'},
                            '2026-10-20T00:00:00Z')
        response.storage_path = 'snapshots/' + response.sha256 + '.html'
        profile = Profile(ORG, 'social-declaration-audit')
        profile.data['legal_identity']['name'] = ENTITY['navn']
        extract(profile, ENTITY, parse_html(body, SITE), response, PROOF)
        profile.data['claims'] = [c for c in profile.data['claims'] if c['field'] == 'company_linked_profile']
        self.assertEqual(len(profile.data['claims']), 1)
        return profile.data, response

    def check(self, data, response):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'snapshots').mkdir()
            (folder / response.storage_path).write_bytes(response.body)
            (folder / 'envelopes.jsonl').write_text(json.dumps(data) + '\n', encoding='utf-8')
            return audit(folder)

    def test_scalar_and_list_declarations_resolve_and_pass_audit(self):
        for scalar in (False, True):
            with self.subTest(scalar=scalar):
                data, response = self.fixture(scalar)
                self.assertTrue(self.check(data, response)['passed'])

    def test_changed_target_platform_and_verification_claim_fail(self):
        data, response = self.fixture()
        for key, value in (('url', 'https://www.linkedin.com/company/99999'),
                           ('url_as_published', 'https://www.linkedin.com/company/99999'),
                           ('platform', 'Facebook'), ('destination_verified', True),
                           ('relationship', 'independently_verified')):
            with self.subTest(key=key):
                changed = copy.deepcopy(data)
                changed['claims'][0]['value'][key] = value
                self.assertFalse(self.check(changed, response)['passed'])

    def test_same_url_declared_by_another_entity_is_not_interchangeable(self):
        data, response = self.fixture()
        eid = data['claims'][0]['evidence_ids'][0]
        evidence = next(e for e in data['evidence'] if e['id'] == eid)
        evidence['selector']['value'] = '/jsonld/0/1/sameAs/0'
        result = self.check(data, response)
        self.assertFalse(result['passed'])
        self.assertTrue(any('verified company organisation' in e['error'] for e in result['errors']))

    def test_unpublished_quote_and_wrong_company_proof_fail(self):
        data, response = self.fixture()
        for key in ('claim_span', 'identity_proof'):
            with self.subTest(key=key):
                changed = copy.deepcopy(data)
                eid = changed['claims'][0]['evidence_ids'][0]
                evidence = next(e for e in changed['evidence'] if e['id'] == eid)
                if key == 'claim_span':
                    evidence[key] = 'A quote that was never on the source page'
                else:
                    evidence[key]['organisation_number'] = '990888213'
                self.assertFalse(self.check(changed, response)['passed'])
