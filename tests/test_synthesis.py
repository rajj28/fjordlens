"""Decision-useful answers must retain period, entity scope and their complete evidence trail."""
import unittest

from fjordlens.answers import QUESTIONS, build_answers, financial_insights
from fjordlens.research import answer_profile, answer_question_keys, screen_profiles
from fjordlens.synthesis import answer, summarize


def claim(cid, field, value, family="financials", year=2025, **extra):
    item = {"id": cid, "field": field, "family": family, "value": value, "availability": "available", "stale": False,
            "scope": "legal_entity", "evidence_ids": ["ev_" + cid]}
    if field.startswith("financial_"):
        item["reporting_period"] = {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"}
    item.update(extra)
    return item


def figure(cid, field, amount, year=2025, **extra):
    value = extra.pop("value", {"amount": str(amount), "currency": "NOK"})
    return claim(cid, "financial_" + field, value, year=year, **extra)


def envelope(claims):
    return {"organisation_number": "923609016", "legal_identity": {"name": "Example AS"}, "claims": claims,
            "evidence": [{"id": "ev_" + c["id"], "source_url": "https://example.no/" + c["id"],
                          "retrieved_at": "2026-10-17T12:00:00Z", "content_sha256": "a" * 64, "claim_span": str(c["value"])} for c in claims],
            "availability": {}, "changes": [], "refresh": {}}


class FinancialSynthesisTests(unittest.TestCase):
    def test_comparable_revenue_growth_cites_both_periods(self):
        row = envelope([figure("r24", "revenue", 1000000, 2024), figure("r25", "revenue", 1250000)])
        sentences, limitations = financial_insights(row)
        self.assertEqual(len(sentences), 1)
        text = sentences[0]["text"]
        self.assertIn("increased by NOK 250 000 (+25.0%)", text)
        self.assertIn("2024-01-01 to 2024-12-31", text)
        self.assertIn("2025-01-01 to 2025-12-31", text)
        self.assertEqual(sentences[0]["claim_ids"], ["r24", "r25"])
        self.assertFalse(limitations)

    def test_loss_to_profit_uses_absolute_change_without_misleading_percentage(self):
        row = envelope([figure("n24", "net_profit", -1000, 2024), figure("n25", "net_profit", 500)])
        sentences, _ = financial_insights(row)
        self.assertIn("increased by NOK 1 500", sentences[0]["text"])
        self.assertNotIn("%", sentences[0]["text"])

    def test_never_compares_partial_period_gap_currency_group_or_stale(self):
        for previous in [
            figure("previous", "revenue", 1000, 2023),
            figure("previous", "revenue", 1000, 2024, scope="consolidated_group"),
            figure("previous", "revenue", 1000, 2024, stale=True),
            figure("previous", "revenue", 1000, 2024, value={"amount": "1000", "currency": "EUR"}),
            figure("previous", "revenue", 1000, 2024, reporting_period={"fraDato": "2024-07-01", "tilDato": "2024-12-31"}),
        ]:
            with self.subTest(previous=previous):
                sentences, limitations = financial_insights(envelope([previous, figure("current", "revenue", 1500)]))
                self.assertFalse(any("Revenue increased" in sentence["text"] for sentence in sentences))
                self.assertTrue(any("Revenue growth is not established" in item["text"] for item in limitations))

    def test_current_sections_exclude_stale_values(self):
        row = envelope([claim("ceo", "registered_role", {"role_code": "DAGL", "name": "Former CEO"}, "leadership", stale=True),
                        figure("stale", "revenue", 987654321, stale=True), figure("assets", "assets", 100)])
        summary = summarize(row)
        self.assertNotIn("error", summary)
        current = [s for block in summary["sections"] if block["key"] != "what_changed" for s in block["sentences"]]
        self.assertFalse(any("Former CEO" in s["text"] or "987 654 321" in s["text"] for s in current))
        self.assertEqual(summary["stale_claim_count"], 2)

    def test_negative_equity_and_profit_do_not_become_solvency_claim(self):
        row = envelope([figure("op", "operating_profit", 100), figure("net", "net_profit", -500), figure("equity", "equity", -900)])
        summary = summarize(row)
        sentences = [s for sec in summary["sections"] if sec["key"] == "financial_context" for s in sec["sentences"]]
        self.assertIn("operating profit and an annual loss", sentences[0]["text"])
        self.assertIn("do not establish current cash flow", sentences[0]["text"])
        self.assertIn("does not by itself establish insolvency or bankruptcy", sentences[1]["text"])
        self.assertEqual(sentences[1]["claim_ids"], ["equity"])

    def test_screen_does_not_backfill_old_revenue_into_latest_period(self):
        row = envelope([figure("r24", "revenue", 1000, 2024), figure("a25", "assets", 100)])
        self.assertEqual(screen_profiles([row], "companies with revenue above 500")["result_count"], 0)

    def test_screen_cannot_treat_euros_as_nok(self):
        row = envelope([figure("eur", "revenue", 1000, value={"amount": "1000", "currency": "EUR"})])
        self.assertEqual(screen_profiles([row], "companies with revenue above NOK 500")["result_count"], 0)

    def test_missing_workplace_count_is_not_a_zero_in_the_total(self):
        row = envelope([claim("w1", "registered_workplace", {"address": {"kommune": "OSLO"}, "employees": 10}, "locations"),
                        claim("w2", "registered_workplace", {"address": {"kommune": "BERGEN"}}, "locations")])
        summary = summarize(row)
        text = " ".join(s["text"] for section in summary["sections"] if section["key"] == "where_it_operates" for s in section["sentences"])
        self.assertIn("some counts are missing", text)
        self.assertNotIn("10 registered employees across them", text)

    def test_minimal_overview_does_not_repeat_workforce_sentence(self):
        row = envelope([claim("employees", "employees", 7, "workforce")])
        summary = summarize(row)
        self.assertEqual(summary["brief"]["text"], "The official registry reports 7 employees.")
        self.assertEqual(summary["brief"]["claim_ids"], ["employees"])


class DirectAnswerTests(unittest.TestCase):
    def test_leadership_returns_names_with_matching_sources(self):
        row = envelope([claim("ceo", "registered_role", {"role_code": "DAGL", "name": "Kari Nord"}, "leadership"),
                        claim("chair", "registered_role", {"role_code": "LEDE", "name": "Ola West"}, "leadership"),
                        claim("member", "registered_role", {"role_code": "MEDL", "name": "Unmentioned"}, "leadership")])
        result = answer(row, "Who leads it?")
        self.assertEqual(result["answer"], "Chief executive: Kari Nord. Board chair: Ola West.")
        self.assertEqual(result["claim_ids"], ["ceo", "chair"])
        self.assertEqual({c["claim_id"] for c in result["citations"]}, {"ceo", "chair"})
        self.assertEqual(result, answer_profile(row, "Who leads it?"))

    def test_location_answer_includes_identity_family_address(self):
        row = envelope([claim("address", "registered_address", {"adresse": ["Gate 1"], "postnummer": "0101", "poststed": "OSLO"}, "identity")])
        result = answer_profile(row, "Where does it operate?")
        self.assertIn("Gate 1, 0101 OSLO", result["answer"])
        self.assertEqual(result["claim_ids"], ["address"])

    def test_overview_does_not_match_review_or_claim_unknown_is_zero(self):
        row = envelope([claim("industry", "industry", {"kode": "62.100", "beskrivelse": "Software development"}, "identity")])
        result = answer_profile(row, "Give me a company overview.")
        self.assertFalse(result["abstained"])
        self.assertIn("Software development", result["answer"])
        unknown = answer_profile(row, "How many employees?")
        self.assertTrue(unknown["abstained"])
        self.assertNotIn("0 employees", unknown["answer"])

    def test_unsupported_prediction_abstains_without_irrelevant_claims(self):
        result = answer_profile(envelope([figure("r25", "revenue", 10)]), "Predict its revenue next year")
        self.assertTrue(result["abstained"])
        self.assertEqual(result["claim_ids"], [])

    def test_filed_answer_contains_both_growth_inputs_and_explicit_scope(self):
        result = answer_profile(envelope([figure("r24", "revenue", 1000, 2024), figure("r25", "revenue", 1500)]), "What are the financial trends?")
        self.assertIn("company accounts", result["answer"])
        self.assertIn("+50.0%", result["answer"])
        self.assertEqual(result["claim_ids"], ["r24", "r25"])

    def test_only_group_accounts_are_explicitly_labeled(self):
        row = envelope([figure("group", "revenue", 1000, scope="consolidated_group")])
        result = answer_profile(row, QUESTIONS[3])
        self.assertIn("consolidated group accounts; separate company figures were not captured", result["answer"])
        self.assertEqual(result["claim_ids"], ["group"])

    def test_distress_question_is_not_answered_as_generic_finances(self):
        self.assertEqual(answer_question_keys("Is it in financial distress?"), [QUESTIONS[10]])
        row = envelope([claim("flag", "bankrupt", False, "identity"), figure("equity", "equity", -500)])
        result = answer_profile(row, "Is it in financial distress?")
        self.assertIn("equity was negative", result["answer"])
        self.assertIn("do not establish current solvency", result["answer"])
        self.assertEqual(result["claim_ids"], ["equity", "flag"])

    def test_first_run_change_answer_does_not_say_zero_changes(self):
        result = answer_profile(envelope([]), "What changed?")
        self.assertTrue(result["abstained"])
        self.assertIn("first run", result["answer"])

    def test_refresh_with_unconfirmed_facts_does_not_claim_they_are_unchanged(self):
        row = envelope([claim("old_headcount", "employees", 7, "workforce", stale=True)])
        row["refresh"] = {"previous_run_id": "previous"}
        result = answer_profile(row, "What changed?")
        self.assertIn("No material change was confirmed", result["answer"])
        self.assertIn("1 earlier fact could not be reconfirmed", result["answer"])
        self.assertEqual(summarize(row)["refresh_note"], result["answer"])
        self.assertEqual(result["claim_ids"], [])


if __name__ == "__main__":
    unittest.main()
