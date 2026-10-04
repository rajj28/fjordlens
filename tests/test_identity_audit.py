import copy
import json
from pathlib import Path
import tempfile
import unittest

from fjordlens.core import Profile, at, validate
from fjordlens.html import parse_html
from fjordlens.identity import assess, jsonld_types, node_org, node_org_identifiers, node_orgs
from fjordlens.net import Budget, Fetcher, Response
from fjordlens.website import extract, research
from scripts.audit_evidence import audit


ORG = "923609016"
OTHER = "990888213"
SITE = "https://equinor.com/"
ENTITY = {"organisasjonsnummer": ORG, "navn": "EQUINOR ASA", "hjemmeside": SITE}
LEGAL_HTML = "<title>Equinor</title><footer>Equinor ASA Org nr 923609016</footer>"


def jsonld(value):
    return '<script type="application/ld+json">' + json.dumps(value) + '</script>'


def response(html, url=SITE):
    return Response(url, url, 200, html.encode(), {"content-type": "text/html"}, "2026-09-22T00:00:00Z")


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, *args, **kwargs):
        return self.responses.get(url, Response(url, url, 404))


class IdentityAuditTests(unittest.TestCase):
    def proof(self):
        return assess(ENTITY, parse_html(LEGAL_HTML, SITE), registry_candidate=True)

    def extract(self, html, url=SITE + "jobs"):
        profile = Profile(ORG, "identity-audit")
        r = response(html, url)
        extract(profile, ENTITY, parse_html(html, url), r, self.proof())
        return profile

    def job(self, employer):
        return {"@type": "JobPosting", "title": "Engineer", "datePosted": "2026-09-01", "hiringOrganization": employer}

    def test_conflicting_employer_cannot_fall_back_to_matching_name(self):
        # Untyped employer objects exercise the job gate independently of the page gate.
        identifiers = [
            {"taxID": OTHER},
            {"vatID": OTHER},
            {"taxID": ORG, "vatID": OTHER},
            {"taxID": [ORG, OTHER]},
            {"identifier": [{"propertyID": "organisasjonsnummer", "value": OTHER}]},
            {"taxID": "malformed-number"},
        ]
        for fields in identifiers:
            with self.subTest(fields=fields):
                profile = self.extract(jsonld(self.job({"name": ENTITY["navn"], **fields})))
                self.assertFalse(any(c["field"] == "job_posting" for c in profile.data["claims"]))

    def test_exact_employer_or_name_without_legal_identifier_still_publishes(self):
        for employer in ({"name": ENTITY["navn"]}, {"name": ENTITY["navn"], "taxID": ORG},
                         {"name": ENTITY["navn"], "taxID": ORG, "vatID": "NO" + ORG + "MVA"}):
            with self.subTest(employer=employer):
                profile = self.extract(jsonld(self.job(employer)))
                self.assertEqual([c["field"] for c in profile.data["claims"]], ["job_posting"])
                self.assertEqual(validate(profile.data), [])

    def test_structured_subpage_conflict_quarantines_all_page_claims(self):
        html = ('<meta name="description" content="This describes the operations of another legal company.">'
                '<a href="mailto:contact@equinor.com">Contact</a>'
                + jsonld({"@graph": [{"@type": "Organization", "name": "OTHER AS", "taxID": OTHER}]}))
        profile = self.extract(html, SITE + "about-other")
        self.assertEqual(profile.data["claims"], [])
        self.assertEqual(profile.data["attempts"][0]["state"], "ambiguous")

    def test_all_valid_identifiers_and_pointers_are_preserved(self):
        node = {"taxID": ORG, "vatID": OTHER,
                "identifier": [{"propertyID": "organisasjonsnummer", "value": ORG}]}
        self.assertEqual(node_orgs(node), {ORG, OTHER})
        self.assertIsNone(node_org(node))
        identifiers = node_org_identifiers(node)
        self.assertEqual([item["pointer"] for item in identifiers], ["/taxID", "/vatID", "/identifier/0/value"])
        for item in identifiers:
            self.assertEqual(at(node, item["pointer"]), item["value"])

    def test_conflicting_identifiers_on_one_organization_cannot_verify_site(self):
        for fields in ({"taxID": ORG, "vatID": OTHER}, {"taxID": [ORG, OTHER]},
                       {"identifier": [{"propertyID": "organisasjonsnummer", "value": ORG},
                                       {"propertyID": "organisasjonsnummer", "value": OTHER}]}):
            with self.subTest(fields=fields):
                html = jsonld({"@type": "Organization", "legalName": ENTITY["navn"], **fields})
                proof = assess(ENTITY, parse_html(html, SITE), registry_candidate=True)
                self.assertFalse(proof["publishable"])
                self.assertEqual(proof["scope"], "multi_entity")
                self.assertEqual(set(proof["observed_organisation_numbers"]), {ORG, OTHER})

    def test_matching_duplicate_identifiers_are_not_a_conflict(self):
        node = {"@type": "Organization", "legalName": ENTITY["navn"], "taxID": ORG, "vatID": "NO" + ORG + "MVA"}
        self.assertEqual(node_org(node), ORG)
        self.assertTrue(assess(ENTITY, parse_html(jsonld(node), SITE), registry_candidate=True)["publishable"])

    def test_malformed_types_do_not_abort_valid_identity_or_publications(self):
        article = {"@type": "NewsArticle", "headline": "Company update", "datePublished": "2026-09-01", "url": SITE + "news/update"}
        for bad_type in (None, 12, {}, [{}], [None, []]):
            with self.subTest(bad_type=bad_type):
                html = LEGAL_HTML + jsonld([{"@type": bad_type}, article])
                self.assertTrue(assess(ENTITY, parse_html(html, SITE), registry_candidate=True)["publishable"])
                profile = self.extract(html)
                self.assertEqual([c["field"] for c in profile.data["claims"]], ["company_publication"])
        self.assertEqual(jsonld_types({"@type": [None, {}, "Organization"]}), {"Organization"})

    def test_subpage_proof_cannot_verify_partial_homepage_name(self):
        entity = {"organisasjonsnummer": ORG, "navn": "NORD AS", "hjemmeside": "https://nordic.example/"}
        home = entity["hjemmeside"]
        html = ('<title>NORDIC GROUP</title>'
                '<meta name="description" content="The Nordic Group owns businesses across many sectors.">'
                '<a href="/contact/nord-as">Contact</a>')
        fetcher = FakeFetcher({home: response(html, home),
                               home + "contact/nord-as": response("<title>NORD AS</title>Nord AS Org nr " + ORG, home + "contact/nord-as")})
        profile = Profile(ORG, "identity-audit")
        research(profile, fetcher, entity, max_pages=2)
        self.assertEqual(profile.data["claims"], [])
        self.assertEqual(profile.data["availability"]["website"]["state"], "ambiguous")

    def test_full_homepage_name_allows_exact_contact_page_proof(self):
        html = '<title>Equinor | Home</title><a href="/contact">Contact</a>'
        fetcher = FakeFetcher({SITE: response(html), SITE + "contact": response(LEGAL_HTML, SITE + "contact")})
        profile = Profile(ORG, "identity-audit")
        research(profile, fetcher, ENTITY, max_pages=2)
        website = next(c for c in profile.data["claims"] if c["field"] == "official_website")
        self.assertEqual(website["value"], SITE)
        evidence = next(e for e in profile.data["evidence"] if e["id"] in website["evidence_ids"])
        self.assertEqual(evidence["source_url"], SITE + "contact")

    def test_structured_company_mention_does_not_prove_source_ownership(self):
        html = ('<title>News about Equinor ASA</title>'
                + jsonld({"@type": "NewsArticle", "about": {"@type": "Organization", "legalName": ENTITY["navn"], "taxID": ORG, "url": "https://news.example/"}}))
        proof = assess(ENTITY, parse_html(html, "https://news.example/"), registry_candidate=False)
        self.assertFalse(proof["publishable"])

    def test_structured_identifier_proof_points_to_original_scalar(self):
        fields_and_pointers = [
            ({"taxID": ["NO" + ORG + "MVA"]}, "/jsonld/0/taxID/0"),
            ({"identifier": {"propertyID": "organisasjonsnummer", "value": ORG}}, "/jsonld/0/identifier/value"),
            ({"identifier": [{"propertyID": "organisasjonsnummer", "value": ORG}]}, "/jsonld/0/identifier/0/value"),
        ]
        for fields, pointer in fields_and_pointers:
            with self.subTest(fields=fields):
                html = jsonld({"@type": "Organization", "legalName": ENTITY["navn"], **fields})
                page = parse_html(html, SITE)
                proof = assess(ENTITY, page, registry_candidate=True)
                self.assertTrue(proof["publishable"])
                self.assertEqual(proof["proof_selector"], {"type": "parsed_html_pointer", "value": pointer})
                self.assertEqual(proof["proof_span"], str(at(page, pointer)))

    def test_structured_legal_evidence_passes_audit_and_wrong_selector_fails(self):
        html = jsonld({"@type": "Organization", "legalName": ENTITY["navn"],
                      "identifier": [{"propertyID": "organisasjonsnummer", "value": ORG}]})
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            r = response(html)
            Fetcher(folder, Budget())._save(r)
            profile = Profile(ORG, "identity-audit")
            research(profile, FakeFetcher({SITE: r}), ENTITY, max_pages=1)
            self.assertEqual([c["field"] for c in profile.data["claims"]], ["official_website"])
            envelope = folder / "envelopes.jsonl"
            envelope.write_text(json.dumps(profile.data) + "\n", encoding="utf-8")
            self.assertTrue(audit(folder)["passed"])
            corrupted = copy.deepcopy(profile.data)
            corrupted["evidence"][0]["selector"]["value"] = "/jsonld/0/legalName"
            envelope.write_text(json.dumps(corrupted) + "\n", encoding="utf-8")
            result = audit(folder)
            self.assertFalse(result["passed"])
            self.assertIn("Structured legal identity evidence", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
