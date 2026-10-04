"""Screening and cited answers follow the closed grammar of Builderr's research-agent suites."""
import unittest

from fjordlens.research import answer_profile, parse_screen_query, screen_profiles

SUITE = [
    ("companies in Oslo with above 7 employees", [("municipality", "eq", "OSLO"), ("employees", ">", 7)]),
    ("companies in Trondheim with employees > 10", [("municipality", "eq", "TRONDHEIM"), ("employees", ">", 10)]),
    ("legal form = STI", [("legal_form", "eq", "STI")]),
    ("organisation form is BRL", [("legal_form", "eq", "BRL")]),
    ("companies with at least 75 employees", [("employees", ">=", 75)]),
    ("companies with revenue above 25 million", [("revenue", ">", 25000000)]),
    ("companies with revenue >= NOK 250 million", [("revenue", ">=", 250000000)]),
    ("companies with less than 2 million revenue", [("revenue", "<", 2000000)]),
]
UNSUPPORTED = ["rank companies by LinkedIn hiring velocity", "find the highest Glassdoor culture scores", "show companies whose web traffic doubled",
               "rank firms by review popularity", "find negative social sentiment", "show companies with the loudest buzz",
               "find firms with no website", "which businesses are probably fraudulent"]


def envelope(org, name, city, employees, revenue):
    ev = {"id": "ev_" + org, "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/" + org, "retrieved_at": "2026-10-04T00:00:00Z",
          "content_sha256": "a" * 64, "claim_span": '"navn":"' + name + '"'}
    claims = [{"id": "c1" + org, "field": "registered_address", "family": "identity", "value": {"kommune": city}, "availability": "available", "evidence_ids": [ev["id"]]},
              {"id": "c2" + org, "field": "employees", "family": "identity", "value": employees, "availability": "available", "evidence_ids": [ev["id"]]}]
    if revenue is not None:
        claims.append({"id": "c3" + org, "field": "financial_revenue", "family": "financials", "scope": "legal_entity",
                       "value": {"amount": str(revenue), "currency": "NOK"}, "reporting_period": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                       "availability": "available", "evidence_ids": [ev["id"]]})
    return {"organisation_number": org, "legal_identity": {"name": name}, "claims": claims, "evidence": [ev], "availability": {}}


class ResearchTests(unittest.TestCase):
    def test_suite_queries_produce_the_oracle_plans(self):
        for query, expected in SUITE:
            with self.subTest(query=query):
                plan = parse_screen_query(query)
                self.assertEqual(sorted((f["field"], f["operator"], f["value"]) for f in plan["filters"]), sorted(expected))
                self.assertTrue(plan["executable"])

    def test_unsupported_queries_abstain(self):
        for query in UNSUPPORTED:
            with self.subTest(query=query):
                self.assertFalse(parse_screen_query(query)["executable"])

    def test_screen_orders_top_n_and_never_treats_missing_as_zero(self):
        rows = [envelope("923609016", "A AS", "OSLO", 10, 5_000_000), envelope("990888213", "B AS", "OSLO", 20, 9_000_000),
                envelope("984862296", "C AS", "OSLO", 30, None), envelope("996819884", "D AS", "BERGEN", 40, 50_000_000)]
        result = screen_profiles(rows, "companies in Oslo with more than 5 employees top 2 by revenue")
        self.assertEqual([r["name"] for r in result["results"]], ["B AS", "A AS"])
        self.assertTrue(all(r["citations"] and r["citations"][0]["content_sha256"] for r in result["results"]))
        low = screen_profiles(rows, "companies with revenue under 6 million")
        self.assertEqual([r["name"] for r in low["results"]], ["A AS"])  # C AS has no filed revenue: not "zero".

    def test_answer_cites_every_fact_and_abstains_on_sentiment(self):
        row = envelope("923609016", "A AS", "OSLO", 10, 5_000_000)
        answer = answer_profile(row, "What revenue can you support?")
        self.assertTrue(answer["facts"])
        self.assertTrue(all(f["source_url"] and f["retrieved_at"] and f["content_sha256"] for f in answer["facts"]))
        self.assertTrue(answer_profile(row, "What is the social sentiment?")["abstained"])


if __name__ == "__main__":
    unittest.main()
