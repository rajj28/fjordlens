import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from fjordlens.core import Profile
from fjordlens.identity import node_org, assess
from fjordlens.html import parse_html
from fjordlens.net import Budget, FetchError, public_unicast
from fjordlens.runner import read_inputs
from test_core import ORG, ENTITY, response

class BoundaryTests(unittest.TestCase):
    def test_false_official_source_rejected(self):
        p=Profile(ORG,'test')
        with self.assertRaises(ValueError):
            p.add('legal_name','EQUINOR ASA',response({'navn':'EQUINOR ASA'},'https://directory.example/'),family='identity',pointer='/navn')

    def test_nonexistent_pointer_rejected(self):
        with self.assertRaises(ValueError):
            Profile(ORG,'test').add('employees',123,response({}),family='identity',pointer='/antallAnsatte')

    def test_company_evidence_requires_proof(self):
        with self.assertRaises(ValueError):
            Profile(ORG,'test').add('business_description','Example',response(b'Example','https://example.com/'),family='description',source_class='company_owned',method='html_text_v1')

    def test_identifier_list_requires_semantics(self):
        self.assertEqual(node_org({'identifier':[{'propertyID':'organisasjonsnummer','value':ORG}]}),ORG)
        self.assertIsNone(node_org({'identifier':[{'propertyID':'DUNS','value':ORG}]}))
        self.assertIsNone(node_org({'taxID':'123'+ORG+'000'}))

    def test_third_party_article_is_not_official_website(self):
        page=parse_html('<title>Equinor ASA | News</title><p>Equinor ASA Org nr 923609016</p>','https://independent-news.example/')
        self.assertFalse(assess(ENTITY,page,registry_candidate=False)['publishable'])

    def test_normalizes_formatted_input(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input.jsonl'
            p.write_text(json.dumps({'organisation_number':'923 609 016'})+'\n')
            self.assertEqual(read_inputs(p),[ORG])

    def test_multicast_and_translation_addresses_blocked(self):
        for value in ('224.0.0.1','ff02::1','64:ff9b::7f00:1','2002:7f00:1::'):
            self.assertFalse(public_unicast(value))
        self.assertTrue(public_unicast('8.8.8.8'))

    def test_concurrent_budget_cannot_overspend(self):
        b=Budget(requests=20,per_company=20)
        def reserve(i):
            try:b.reserve(str(i),'https://example.com/')
            except FetchError:pass
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(reserve,range(100)))
        self.assertEqual(b.total,20)
        self.assertEqual(len(b.receipts),20)

if __name__=='__main__':unittest.main()
