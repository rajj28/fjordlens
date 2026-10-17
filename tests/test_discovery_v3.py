"""Candidate recall may widen, but ownership, query cost and URL safety do not weaken."""
import os
import unittest
from unittest import mock

from fjordlens import discovery, gate, search
from fjordlens.html import parse_html
from test_search import ENTITY, ORG, FakeFetcher, FakeResponse, query_of


class DiscoveryV3Tests(unittest.TestCase):
    def test_initialism_guess_is_bounded_and_does_not_replace_primary_name(self):
        domains = discovery.guess_domains("NORSK INDUSTRI TEKNIKK AS")
        self.assertEqual(domains[0], "norskindustriteknikk.no")
        self.assertIn("nit.no", domains)
        self.assertLessEqual(len(domains), 6)
        self.assertEqual(discovery.guess_domains("NORSK INDUSTRI TEKNIKK AS", 0), [])
        self.assertNotIn("ni.no", discovery.guess_domains("NORSK INDUSTRI AS"))

    def test_registered_trading_name_after_branch_marker_is_recovered(self):
        brands = ["FJORDKRAFT TEKNIKK AS", "FJORDKRAFT TEKNIKK AS AVD BERGEN",
                  "FJORDKRAFT TEKNIKK AS AVD RESTAURANT LYKKE", "RESTAURANT LYKKE AVD TRONDHEIM",
                  "NORDLYS DESIGN", "ANOTHER BRAND"]
        self.assertEqual(discovery.registered_brands(ENTITY, brands), ["RESTAURANT LYKKE", "NORDLYS DESIGN"])
        with mock.patch.object(discovery, "resolves", side_effect=lambda d: d == "restaurantlykke.no"):
            candidates, _ = discovery.candidates(ENTITY, brands=brands)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://restaurantlykke.no/")
        self.assertEqual(candidates[0]["origin"], "dns_guess_brand")
        self.assertEqual(candidates[0]["guessed_from"], "registered_workplace_name")

    def test_search_uses_native_legal_name_and_distinct_registered_brand(self):
        entity = {**ENTITY, "navn": "VIKØREN EIENDOM AS"}
        queries = search.queries(entity, brands=["VIKØREN EIENDOM AS", "RESTAURANT LYKKE"])
        self.assertEqual([q["query_kind"] for q in queries],
                         ["organisation_number", "legal_name", "registered_workplace_name", "ownership_page"])
        self.assertIn('"VIKØREN EIENDOM AS"', queries[1]["q"])
        self.assertIn('"RESTAURANT LYKKE"', queries[2]["q"])
        self.assertIn(ORG, queries[3]["q"])
        self.assertIn("-site:proff.no", queries[3]["q"])
        self.assertEqual(len(search.queries(entity, brands=["a", "b", "c"], max_queries=100)), 4)
        self.assertEqual(search.queries(entity, max_queries=0), [])

    @mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-only"}, clear=True)
    def test_search_keeps_legal_page_even_after_root_and_other_same_domain_results(self):
        fetcher = FakeFetcher([["https://brand.no/", "https://brand.no/news/", "https://brand.no/about/",
                                "https://brand.no/contact/", "https://brand.no/privacy/", "https://brand.no/legal-notice/"]])
        candidate = next(search.nomination_batches(fetcher, ENTITY))[0]
        self.assertEqual(candidate["verification_url"], "https://brand.no/legal-notice/")
        self.assertEqual(candidate["verification_urls"], ["https://brand.no/legal-notice/", "https://brand.no/privacy/",
                                                         "https://brand.no/contact/"])
        self.assertEqual(len(fetcher.calls), 1)

    @mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-only"}, clear=True)
    def test_registered_brand_fallback_is_lazy_and_transient(self):
        fetcher = FakeFetcher([[], [], ["https://lykke.no/?page=contact"], []])
        batches = search.nomination_batches(fetcher, ENTITY, brands=["RESTAURANT LYKKE"])
        self.assertEqual(next(batches), [])
        self.assertEqual(next(batches), [])
        candidate = next(batches)[0]
        self.assertEqual(candidate["origin"], "search_name")
        self.assertEqual(candidate["search_query_kind"], "registered_workplace_name")
        self.assertEqual(candidate["verification_url"], "https://lykke.no/?page=contact")
        self.assertIn("RESTAURANT LYKKE", query_of(fetcher.calls[-1]))
        batches.close()
        self.assertEqual(len(fetcher.calls), 3)
        self.assertTrue(all(call[2]["store"] is False and call[2]["cost"] > 0 for call in fetcher.calls))

    @mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-only"}, clear=True)
    def test_query_and_candidate_caps_survive_high_caller_limits(self):
        fetcher = FakeFetcher([[f"https://brand{i}.no/" for i in range(12)]] * 8)
        batches = list(search.nomination_batches(fetcher, ENTITY, brands=["RESTAURANT LYKKE"], limit=100, max_queries=100))
        self.assertEqual(len(fetcher.calls), 4)
        self.assertTrue(all(len(batch) <= 3 for batch in batches))

    @mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-only"}, clear=True)
    def test_blocked_search_does_not_spend_on_fallback_queries(self):
        fetcher = FakeFetcher([])
        fetcher.get = mock.Mock(return_value=FakeResponse({}, state="blocked"))
        self.assertEqual(list(search.nomination_batches(fetcher, ENTITY, brands=["RESTAURANT LYKKE"])), [])
        self.assertEqual(fetcher.get.call_count, 1)

    @mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-only"}, clear=True)
    def test_malformed_results_cannot_break_company_envelope(self):
        fetcher = FakeFetcher([])
        fetcher.get = mock.Mock(side_effect=[FakeResponse([]), FakeResponse({"web": []}),
                                             FakeResponse({"web": {"results": "unexpected"}})])
        self.assertEqual(list(search.nomination_batches(fetcher, ENTITY)), [])

    def test_unsafe_or_restricted_urls_are_still_excluded(self):
        for url in ("https://proff.no/company", "https://facebook.com/company", "https://user:pass@brand.no/",
                    "javascript:alert(1)", "https://[invalid/", "https://brand.no:bad/"):
            with self.subTest(url=url):
                self.assertFalse(search.usable(url, ENTITY, False, ()))

    def test_acronym_domain_is_not_proof_but_exact_owner_number_is(self):
        url = "https://nit.no/"
        candidate = {"url": url, "origin": "search_name"}
        entity = {**ENTITY, "navn": "NORSK INDUSTRI TEKNIKK AS"}
        body = "<title>NIT</title><p>Vi leverer elektro og automasjon til kunder i hele Norge.</p>" * 3
        ctx = gate.Context(entity)
        self.assertFalse(gate.assess(ctx, candidate, [parse_html(body, url)])["publishable"])
        owned = body + f"<footer>Norsk Industri Teknikk AS · Org.nr. {ORG}</footer>"
        accepted = gate.assess(ctx, candidate, [parse_html(owned, url)])
        self.assertTrue(accepted["publishable"], accepted)
        self.assertTrue(accepted["rule"].startswith("A:"))
        wrong_owner = body + "<footer>Another Company AS · Org.nr. 984851006</footer>"
        rejected = gate.assess(ctx, candidate, [parse_html(wrong_owner, url)])
        self.assertFalse(rejected["publishable"])
        self.assertTrue(rejected["hard_reject"])


if __name__ == "__main__":
    unittest.main()
