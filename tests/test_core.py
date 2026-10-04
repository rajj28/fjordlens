import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fjordlens.core import Profile, org_number, validate
from fjordlens.html import parse_html
from fjordlens.identity import assess, legal_numbers
from fjordlens.net import Budget, FetchError, Fetcher, Response, resolve_public, safe_url
from fjordlens import official
from fjordlens.refresh import refresh, semantic

ORG = "923609016"
ENTITY = {"organisasjonsnummer": ORG, "navn": "EQUINOR ASA", "hjemmeside": "equinor.com"}

def response(body, url=None, status=200):
    raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
    return Response(url or official.BASE + "/enheter/" + ORG, url or official.BASE + "/enheter/" + ORG,
                    status, raw, {"content-type": "application/json"}, "2026-09-22T00:00:00Z")

class FakeFetcher:
    def __init__(self, value): self.value = value
    def get(self, *args, **kwargs): return self.value

class IdentityTests(unittest.TestCase):
    def page(self, body):
        return parse_html('<title>Equinor</title>' + body, "https://equinor.com/")

    def test_checksum(self):
        self.assertEqual(org_number("923 609 016"), ORG)
        for number in ("923609017", "111", "NO923609016", "923609016000"):
            with self.assertRaises(ValueError): org_number(number)

    def test_labeled_org_accepts(self):
        result = assess(ENTITY, self.page('<footer>Equinor ASA Org.nr. 923 609 016 MVA</footer>'), registry_candidate=True)
        self.assertTrue(result["publishable"])

    def test_digits_without_legal_context_rejected(self):
        self.assertFalse(assess(ENTITY, self.page('Invoice 923609016. EQUINOR ASA'), registry_candidate=True)["publishable"])

    def test_long_number_not_truncated(self):
        self.assertNotIn(ORG, legal_numbers('Org nr 92360901645'))

    def test_parent_group_conflict_rejected(self):
        page = self.page('Equinor ASA Org nr 923609016. Equinor Energy AS Org nr 990888213')
        self.assertEqual(assess(ENTITY, page, registry_candidate=True)["scope"], "multi_entity")

    def test_same_name_not_identity(self):
        self.assertFalse(assess(ENTITY, self.page('Equinor ASA in Stavanger'), registry_candidate=True)["publishable"])

    def test_corroboration_requires_all_factors(self):
        entity = {**ENTITY, "forretningsadresse": {"adresse": ["Forusbeen 50"], "postnummer": "4035"}, "telefon": "51 99 00 00"}
        page = self.page('Equinor ASA Forusbeen 50 4035 Stavanger Telefon: 51 99 00 00')
        self.assertTrue(assess(entity, page, registry_candidate=True)["publishable"])
        self.assertFalse(assess(entity, page, registry_candidate=False)["publishable"])
        wrong = {**entity, "telefon": "51 99 00 01"}
        self.assertFalse(assess(wrong, page, registry_candidate=True)["publishable"])
        wrong = {**entity, "forretningsadresse": {"adresse": ["Forusbeen 51"], "postnummer": "4035"}}
        self.assertFalse(assess(wrong, page, registry_candidate=True)["publishable"])

    def test_parked_rejected(self):
        self.assertFalse(assess(ENTITY, self.page('Equinor ASA Org nr 923609016. This domain is for sale'), registry_candidate=True)["publishable"])

    def test_jsonld_exact_organization(self):
        html = '<script type="application/ld+json">{"@type":"Organization","legalName":"EQUINOR ASA","taxID":"NO923609016MVA"}</script>'
        self.assertTrue(assess(ENTITY, self.page(html), registry_candidate=True)["publishable"])

class FinancialTests(unittest.TestCase):
    def record(self, org=ORG):
        return {"virksomhet": {"organisasjonsnummer": org}, "regnskapstype": "SELSKAP", "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                "valuta": "NOK", "resultatregnskapResultat": {"aarsresultat": 0, "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": "1234567890123456.78"}}}}

    def test_wrong_entity_rejected(self):
        p = Profile(ORG, "test")
        official.financials(p, FakeFetcher(response([self.record("990888213")])))
        self.assertEqual(p.data["claims"], [])

    def test_zero_is_supported_missing_is_not_zero(self):
        p = Profile(ORG, "test")
        official.financials(p, FakeFetcher(response([self.record()])))
        by = {c["field"]: c for c in p.data["claims"]}
        self.assertEqual(by["financial_net_profit"]["value"]["amount"], "0")
        self.assertNotIn("financial_assets", by)
        self.assertEqual(by["financial_revenue"]["value"]["amount"], "1234567890123456.78")
        self.assertEqual(validate(p.data), [])

    def test_group_accounts_are_labeled(self):
        row = self.record()
        row["regnskapstype"] = "KONSERN"
        p = Profile(ORG, "test")
        official.financials(p, FakeFetcher(response([row])))
        self.assertTrue(all(c["scope"] == "consolidated_group" for c in p.data["claims"]))

    def test_missing_period_rejected(self):
        row = self.record()
        row.pop("regnskapsperiode")
        p = Profile(ORG, "test")
        official.financials(p, FakeFetcher(response([row])))
        self.assertEqual(p.data["claims"], [])

class RefreshTests(unittest.TestCase):
    def test_money_formatting_is_not_a_change(self):
        self.assertEqual(semantic({'amount':'123.00','currency':'NOK','record_id':1}),semantic({'amount':'123','currency':'NOK','record_id':2}))

    def test_malformed_official_response_cannot_remove_roles(self):
        p = Profile(ORG, 'second')
        official.leadership(p, FakeFetcher(response({})))
        self.assertEqual(p.data['availability']['leadership']['state'], 'failed')
        self.assertFalse(p.data['availability']['leadership'].get('complete'))

    def profile(self, value=12, run="first"):
        p = Profile(ORG, run)
        r = response({"antallAnsatte": value})
        p.add("employees", value, r, family="identity", pointer="/antallAnsatte")
        p.data["run"]["completed_at"] = "2026-09-22T00:00:00Z"
        return refresh(p.data)

    def test_identical_source_is_idempotent(self):
        old = self.profile()
        current = refresh(self.profile(run="second"), old)
        self.assertEqual(current["changes"], [])
        self.assertEqual(len(current["claims"]), 1)

    def test_real_change_has_both_evidence_sides(self):
        new = refresh(self.profile(13, "second"), self.profile())
        self.assertEqual(len(new["changes"]), 1)
        event = new["changes"][0]
        self.assertEqual((event["previous_value"], event["current_value"]), (12, 13))
        self.assertTrue(event["previous_evidence_ids"] and event["current_evidence_ids"])
        self.assertEqual(validate(new), [])

    def test_failed_refresh_preserves_evidence_without_change(self):
        current = Profile(ORG, "second")
        current.state("identity", "failed", "Timeout")
        current.data["run"]["completed_at"] = "2026-09-22T00:00:00Z"
        updated = refresh(current.data, self.profile())
        self.assertTrue(updated["claims"][0]["stale"])
        self.assertEqual(updated["changes"], [])
        self.assertEqual(validate(updated), [])

    def test_wrong_entity_diff_rejected(self):
        previous = self.profile()
        previous["organisation_number"] = "990888213"
        with self.assertRaises(ValueError): refresh(self.profile(), previous)

class NetworkTests(unittest.TestCase):
    def test_bad_urls_rejected(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'http://169.254.169.254/latest', 'https://user:password@example.com/', 'http://localhost/', 'http://example.com:8080/', 'http://example.com\\@localhost/'):
            with self.subTest(url=url), self.assertRaises((FetchError, ValueError)):
                safe_url(url)

    def test_mixed_public_private_dns_rejected(self):
        addresses = [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=addresses), self.assertRaises(FetchError):
            resolve_public('example.com', 443)

    def test_budget_exact_ceiling(self):
        budget = Budget(requests=2, per_company=2)
        budget.reserve(ORG, 'https://example.com/1')
        budget.reserve(ORG, 'https://example.com/2')
        with self.assertRaises(FetchError): budget.reserve(ORG, 'https://example.com/3')
        self.assertEqual(budget.total, 2)

    def test_replay_hash_tampering_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fetcher(Path(directory), Budget())
            r = response({"x": 1}, "https://example.com/")
            f._save(r)
            replay = Fetcher(Path(directory), Budget(), replay=Path(directory))
            self.assertEqual(replay.get(r.url, ORG).json(), {"x": 1})
            self.assertEqual(replay.budget.total, 0)

    def test_robots_disallow_blocks_target(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fetcher(Path(directory), Budget())
            with patch.object(f, '_raw', return_value=response(b'User-agent: *\nDisallow: /', 'https://example.com/robots.txt')) as raw:
                self.assertEqual(f.get('https://example.com/', ORG).state, 'blocked')
                self.assertEqual(raw.call_count, 1)

class ParserTests(unittest.TestCase):
    def test_scripts_excluded_and_relative_links(self):
        p = parse_html('<script>ignore me</script><a href="/news">Our <strong>news</strong></a>', 'https://example.com/')
        self.assertNotIn('ignore me', p['text'])
        self.assertEqual(p['links'][0]['url'], 'https://example.com/news')
        self.assertEqual(p['links'][0]['text'], 'Our news')

    def test_bad_json_and_external_base(self):
        p = parse_html('<base href="http://evil.test"><script type="application/ld+json">bad{</script><a href="/x">x</a>', 'https://example.com/')
        self.assertEqual(p['jsonld'], [])
        self.assertEqual(p['links'][0]['url'], 'https://example.com/x')

if __name__ == '__main__': unittest.main()
