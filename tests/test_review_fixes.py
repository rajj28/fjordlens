"""Regression tests for the 5 Oct review (Codex Astra): wrong-company paths, verbatim quotes, refresh, budgets."""
import json
import os
import unittest
from unittest import mock

from fjordlens import gate, net, search
from fjordlens.answers import QUESTIONS, build_answers
from fjordlens.core import Profile, validate
from fjordlens.html import parse_html
from fjordlens.identity import assess
from fjordlens.net import Budget, FetchError, Response
from fjordlens.observations import to_observations
from fjordlens.website import extract, wordpress_posts

ORG = "923609016"
SITE = "https://equinor.com/"
ENTITY = {"organisasjonsnummer": ORG, "navn": "EQUINOR ASA", "hjemmeside": SITE}
LEGAL_HTML = "<title>Equinor</title><footer>Equinor ASA Org nr 923609016</footer>"


def response(html, url=SITE, content_type="text/html"):
    body = html.encode() if isinstance(html, str) else html
    return Response(url, url, 200, body, {"content-type": content_type}, "2026-10-05T00:00:00Z")


def jsonld(value):
    return '<script type="application/ld+json">' + json.dumps(value) + "</script>"


class SiteExtractionTests(unittest.TestCase):
    def proof(self):
        return assess(ENTITY, parse_html(LEGAL_HTML, SITE), registry_candidate=True)

    def extract(self, html, url):
        profile = Profile(ORG, "review-fixes")
        extract(profile, ENTITY, parse_html(html, url), response(html, url), self.proof())
        return profile

    def fields(self, profile, field):
        return [c for c in profile.data["claims"] if c["field"] == field]

    def test_sameas_of_a_longer_named_sibling_is_not_ours(self):
        html = "<title>Equinor</title>" + jsonld({"@type": "Organization", "name": "EQUINOR EIENDOM ASA",
                                                  "sameAs": ["https://www.linkedin.com/company/equinoreiendom"]})
        self.assertEqual(self.fields(self.extract(html, SITE), "company_linked_profile"), [])

    def test_sameas_of_our_exact_name_is_published(self):
        html = "<title>Equinor</title>" + jsonld({"@type": "Organization", "name": "Equinor ASA",
                                                  "sameAs": ["https://www.linkedin.com/company/equinor"]})
        claims = self.fields(self.extract(html, SITE), "company_linked_profile")
        self.assertEqual([c["value"]["url"] for c in claims], ["https://www.linkedin.com/company/equinor"])

    def test_sameas_with_conflicting_identifier_is_rejected(self):
        html = "<title>Equinor</title>" + jsonld({"@type": "Organization", "name": "Equinor ASA", "taxID": "990888213",
                                                  "sameAs": ["https://www.linkedin.com/company/equinor"]})
        self.assertEqual(self.fields(self.extract(html, SITE), "company_linked_profile"), [])

    def test_contact_phone_quote_is_verbatim_source(self):
        html = "<title>Kontakt oss</title><p>Telefon: 51&nbsp;99&nbsp;00&nbsp;00</p>"
        profile = self.extract(html, SITE + "kontakt")
        phones = self.fields(profile, "website_phone")
        self.assertEqual(len(phones), 1)
        evidence = next(e for e in profile.data["evidence"] if e["id"] in phones[0]["evidence_ids"])
        self.assertIn(evidence["claim_span"], html)
        self.assertEqual(evidence["claim_span"], "51&nbsp;99&nbsp;00&nbsp;00")
        self.assertEqual(validate(profile.data), [])

    def test_third_party_numbers_on_the_contact_page_are_not_ours(self):
        for block in ("<h2>Regnskapsfører</h2><p>Tall og Tekst</p><p>Telefon: 22 33 44 55</p>",
                      "<h2>Vår partner Bygg Service AS</h2><p>Telefon: 22 33 44 55</p>"):
            with self.subTest(block=block):
                profile = self.extract("<title>Kontakt oss</title>" + block, SITE + "kontakt")
                self.assertEqual(self.fields(profile, "website_phone"), [])

    def test_our_own_name_next_to_the_number_is_fine(self):
        profile = self.extract("<title>Kontakt oss</title><p>Equinor ASA</p><p>Telefon: 51 99 00 00</p>", SITE + "kontakt")
        self.assertEqual(len(self.fields(profile, "website_phone")), 1)


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, *args, **kwargs):
        return self.responses.get(url, Response(url, url, 404))


class WordPressHostTests(unittest.TestCase):
    API = SITE + "wp-json/wp/v2/posts?per_page=10&_fields=id,date,link,title"

    def run_posts(self, api_response):
        profile = Profile(ORG, "review-fixes")
        proof = assess(ENTITY, parse_html(LEGAL_HTML, SITE), registry_candidate=True)
        home = response('<link rel="https://api.w.org/" href="' + SITE + 'wp-json/">' + LEGAL_HTML)
        wordpress_posts(profile, FakeFetcher({self.API: api_response}), ORG, home, proof)
        return [c for c in profile.data["claims"] if c["field"] == "company_publication"]

    def posts(self, link):
        return json.dumps([{"id": 1, "date": "2026-09-01T10:00:00", "link": link, "title": {"rendered": "Ny avtale med kunde"}}]).encode()

    def test_same_host_post_is_published(self):
        claims = self.run_posts(response(self.posts(SITE + "ny-avtale/"), self.API, "application/json"))
        self.assertEqual([c["value"]["url"] for c in claims], [SITE + "ny-avtale/"])

    def test_api_redirected_to_another_host_is_ignored(self):
        moved = Response(self.API, "https://eiendom.equinor.com/wp-json/wp/v2/posts", 200,
                         self.posts("https://eiendom.equinor.com/sak/"), {"content-type": "application/json"}, "2026-10-05T00:00:00Z")
        self.assertEqual(self.run_posts(moved), [])

    def test_post_on_another_host_is_ignored(self):
        claims = self.run_posts(response(self.posts("https://eiendom.equinor.com/sak/"), self.API, "application/json"))
        self.assertEqual(claims, [])


def claim(cid, field, family, value, **extra):
    base = {"id": cid, "field": field, "family": family, "value": value, "availability": "available", "stale": False,
            "evidence_ids": ["ev_" + cid], "primary_evidence_id": "ev_" + cid, "source_url": "https://x.no/",
            "retrieved_at": "2026-10-05T10:00:00Z", "content_sha256": "a" * 64, "claim_span": "q", "source_class": "official_registry",
            "snapshot_path": "snapshots/" + "a" * 64 + ".json", "scope": "legal_entity", "reporting_period": None}
    base.update(extra)
    return base


def envelope(claims, **extra):
    env = {"organisation_number": ORG, "claims": claims, "evidence": [], "availability": {}, "changes": [], "refresh": {}}
    env.update(extra)
    return env


class ExportAndAnswerTests(unittest.TestCase):
    def test_workplace_headcount_is_not_exported_as_the_entity(self):
        env = envelope([claim("e1", "employees", "workforce", 100),
                        claim("w1", "workplace_employees", "workforce", 10, scope="registered_subunit")])
        self.assertEqual([o["claim_id"] for o in to_observations(env)], ["e1"])

    def test_removed_fact_is_cited_by_evidence_not_by_a_missing_claim(self):
        change = {"type": "removed_role", "field": "registered_role", "material": True, "claim_id": "gone",
                  "previous_evidence_ids": ["ev_old"], "current_evidence_ids": []}
        answer = build_answers(envelope([], changes=[change], refresh={"previous_run_id": "r1"}))[QUESTIONS.index("What changed since the last check?")]
        self.assertEqual((answer["claim_ids"], answer["evidence_ids"]), ([], ["ev_old"]))

    def test_full_year_and_part_year_ending_together_are_not_mixed(self):
        year = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
        half = {"fraDato": "2025-07-01", "tilDato": "2025-12-31"}
        claims = [claim("r", "financial_revenue", "financials", {"amount": "1000", "currency": "NOK"}, reporting_period=year),
                  claim("n", "financial_net_profit", "financials", {"amount": "-50", "currency": "NOK"}, reporting_period=half)]
        answer = build_answers(envelope(claims))[QUESTIONS.index("What are its latest filed numbers?")]
        self.assertIn("2025-01-01 to 2025-12-31", answer["answer"])
        self.assertEqual(answer["claim_ids"], ["r"])


SEARCH_ENTITY = {"organisasjonsnummer": ORG, "navn": "FJORDKRAFT TEKNIKK AS",
                 "forretningsadresse": {"adresse": ["Strandgaten 5"], "postnummer": "5013", "poststed": "BERGEN"}}


class GateReviewTests(unittest.TestCase):
    def decide(self, html, url, origin):
        return gate.assess(gate.Context(SEARCH_ENTITY), {"url": url, "origin": origin}, [parse_html(html, url)])

    def test_supplier_site_found_by_org_number_search_is_not_ours(self):
        html = ("<title>Nordlys Design</title><p>Vi lager nettsider for bedrifter i Bergen.</p>" * 3
                + '<h2>Kunder</h2><img class="client-logo" src="/client-logo.png" alt="Fjordkraft Teknikk AS">'
                + "<p>Fjordkraft Teknikk AS, Strandgaten 5, 5013 Bergen</p>")
        self.assertFalse(self.decide(html, "https://nordlys.no/", "search_orgnr")["publishable"])

    def test_rule_d_acceptance_quotes_the_registered_address(self):
        html = ("<title>Fjordkraft Teknikk AS</title><p>Vi leverer elektro og automasjon.</p>" * 3
                + "<footer>Fjordkraft Teknikk AS, Strandgaten 5, 5013 Bergen</footer>")
        result = self.decide(html, "https://fjordkraftteknikk.no/", "dns_guess")
        self.assertTrue(result["publishable"], result)
        self.assertIn("5013", result["proof_span"])

    def test_rule_d_proof_prefers_the_postcode_written_with_the_town(self):
        html = ("<title>Fjordkraft Teknikk AS</title><p>Servicepakke 5013 kroner.</p>"
                + "<p>Vi leverer elektro, automasjon og service til industri i hele Vestland.</p>" * 6
                + "<footer>Fjordkraft Teknikk AS, Strandgaten 5, 5013 Bergen</footer>")
        result = self.decide(html, "https://fjordkraftteknikk.no/", "dns_guess")
        self.assertTrue(result["publishable"], result)
        self.assertIn("5013 Bergen", result["proof_span"])
        self.assertNotIn("kroner", result["proof_span"])


class BudgetConfigTests(unittest.TestCase):
    def test_invalid_cost_configuration_falls_back_to_safe_defaults(self):
        with mock.patch.dict(os.environ, {"FJORDLENS_MAX_API_COST_USD": "nan", "BRAVE_COST_PER_REQUEST_USD": "-1"}):
            self.assertEqual(Budget().max_cost, 9.0)
            self.assertEqual(search.cost_per_query(), 0.005)
        with mock.patch.dict(os.environ, {"BRAVE_COST_PER_REQUEST_USD": "inf"}):
            self.assertEqual(search.cost_per_query(), 0.005)

    def test_invalid_declared_cost_is_refused(self):
        budget = Budget()
        for cost in (float("nan"), float("inf"), -0.01):
            with self.subTest(cost=cost):
                with self.assertRaises(FetchError):
                    budget.reserve(ORG, "https://api.search.brave.com/res/v1/web/search", cost)


if __name__ == "__main__":
    unittest.main()
