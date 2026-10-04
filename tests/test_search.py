"""Optional Brave search nomination: key-gated, transient, and unable to bypass the identity gate."""
import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from fjordlens import gate, search
from fjordlens.html import parse_html

ORG = "923609016"
ENTITY = {"organisasjonsnummer": ORG, "navn": "FJORDKRAFT TEKNIKK AS",
          "forretningsadresse": {"adresse": ["Strandgaten 5"], "postnummer": "5013", "poststed": "BERGEN"}}


class FakeResponse:
    def __init__(self, payload, state="available"):
        self.payload, self.state = payload, state

    def json(self):
        return self.payload


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, org, **kwargs):
        self.calls.append((url, org, kwargs))
        return FakeResponse({"web": {"results": [{"url": u} for u in self.pages.pop(0)]}} if self.pages else {}, "available")


def query_of(call):
    return parse_qs(urlsplit(call[0]).query)["q"][0]


class SearchTests(unittest.TestCase):
    def test_inactive_without_a_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            fetcher = FakeFetcher([["https://fjordkraftteknikk.no/"]])
            self.assertFalse(search.enabled())
            self.assertEqual(search.nominate(fetcher, ENTITY), [])
            self.assertEqual(fetcher.calls, [])

    def test_org_number_query_first_and_transient(self):
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "k"}, clear=True):
            fetcher = FakeFetcher([["https://www.proff.no/selskap/x", "https://www.facebook.com/fjordkraft",
                                    "https://www.fjordkraftteknikk.no/om-oss/", "https://www.fjordkraftteknikk.no/kontakt",
                                    "https://kunde.no/referanser"]])
            found = search.nominate(fetcher, ENTITY)
        self.assertEqual([c["url"] for c in found], ["https://www.fjordkraftteknikk.no/", "https://kunde.no/"])
        self.assertEqual({c["origin"] for c in found}, {"search_orgnr"})
        self.assertEqual(len(fetcher.calls), 1)  # Follow-up name query only when the number finds nothing.
        url, org, kwargs = fetcher.calls[0]
        self.assertIn(ORG, query_of(fetcher.calls[0]))
        self.assertIn("923 609 016", query_of(fetcher.calls[0]))
        self.assertEqual((org, kwargs["store"], kwargs["headers"]["X-Subscription-Token"]), (ORG, False, "k"))
        self.assertGreater(kwargs["cost"], 0)

    def test_name_query_needs_the_name_in_the_domain(self):
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "k"}, clear=True):
            fetcher = FakeFetcher([[], ["https://www.byggfirma.no/", "https://fjordkraft-teknikk.no/", "https://teknikk.no/"]])
            found = search.nominate(fetcher, ENTITY)
        self.assertEqual(len(fetcher.calls), 2)
        self.assertIn("BERGEN".title(), query_of(fetcher.calls[1]))
        urls = [c["url"] for c in found]
        self.assertEqual(urls, ["https://fjordkraft-teknikk.no/"])  # "teknikk.no" lacks the distinctive "fjordkraft".
        self.assertTrue(all(c["origin"] == "search_name" for c in found))

    def test_already_tried_domains_are_not_renominated(self):
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "k"}, clear=True):
            fetcher = FakeFetcher([["https://www.fjordkraftteknikk.no/", "https://annen.no/"]])
            found = search.nominate(fetcher, ENTITY, exclude={"fjordkraftteknikk.no"})
        self.assertEqual([c["url"] for c in found], ["https://annen.no/"])


class SearchGateTests(unittest.TestCase):
    """Nominations get no credit for being search results: only rule A or D can accept them."""

    def decide(self, html, url, origin):
        ctx = gate.Context(ENTITY)
        return gate.assess(ctx, {"url": url, "origin": origin}, [parse_html(html, url)])

    def test_name_only_site_from_search_is_rejected(self):
        html = "<title>Fjordkraft Teknikk AS</title><p>Vi leverer elektro og automasjon til industri i hele Norge.</p>" * 3
        for origin in ("search_orgnr", "search_name"):
            self.assertFalse(self.decide(html, "https://fjordkraftteknikk.no/", origin)["publishable"], origin)

    def test_org_number_in_owner_position_is_accepted(self):
        html = ("<title>Fjordkraft Teknikk AS</title><p>Vi leverer elektro og automasjon.</p>" * 3
                + f"<footer>Fjordkraft Teknikk AS · Org.nr. {ORG}</footer>")
        result = self.decide(html, "https://fjordkraftteknikk.no/", "search_orgnr")
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("A"))

    def test_name_with_registered_address_is_accepted_by_rule_d(self):
        html = ("<title>Fjordkraft Teknikk AS</title><p>Vi leverer elektro og automasjon.</p>" * 3
                + "<footer>Fjordkraft Teknikk AS, Strandgaten 5, 5013 Bergen</footer>")
        result = self.decide(html, "https://fjordkraftteknikk.no/", "search_name")
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("D"))


if __name__ == "__main__":
    unittest.main()
