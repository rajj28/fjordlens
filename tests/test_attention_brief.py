"""The overview attention brief reports sourced changes, risks and capture limits without scoring them."""
import unittest

from fjordlens.synthesis import summarize


def claim(cid, field, value, family, *, stale=False, evidence=None, period=None, scope="legal_entity"):
    return {
        "id": cid, "field": field, "value": value, "family": family,
        "availability": "available", "stale": stale, "scope": scope,
        "evidence_ids": [evidence or f"ev_{cid}"],
        **({"reporting_period": period} if period else {}),
    }


def profile(claims=(), **extra):
    row = {"organisation_number": "923609016", "legal_identity": {"name": "Example AS"},
           "claims": list(claims), "evidence": [], "availability": {}, "changes": [], "refresh": {}}
    row.update(extra)
    return row


class AttentionBriefTests(unittest.TestCase):
    def test_material_change_and_registered_risk_keep_their_source_links(self):
        row = profile(
            [claim("bankruptcy", "bankrupt", True, "identity"),
             claim("address", "registered_address", {"poststed": "OSLO"}, "identity")],
            refresh={"previous_run_id": "previous-run"},
            changes=[{"id": "change-address", "type": "changed_address", "field": "registered_address",
                      "material": True, "claim_id": "address", "previous_value": "BERGEN",
                      "current_value": "OSLO", "previous_evidence_ids": ["ev_before"],
                      "current_evidence_ids": ["ev_address"], "observed_at": "2026-10-19"}],
        )
        attention = summarize(row)["attention"]
        self.assertEqual(attention["comparison"], "compared")
        self.assertEqual(attention["changes"][0]["previous_evidence_ids"], ["ev_before"])
        self.assertEqual(attention["changes"][0]["current_evidence_ids"], ["ev_address"])
        self.assertEqual(attention["risks"][0]["kind"], "official_registry_flag")
        self.assertEqual(attention["risks"][0]["claim_ids"], ["bankruptcy"])
        self.assertNotIn("score", " ".join(attention["risks"][0].keys()).lower())

    def test_negative_equity_is_a_period_scoped_warning_not_a_solvency_conclusion(self):
        equity = claim("equity", "financial_equity", {"amount": "-500", "currency": "NOK"}, "financials",
                       period={"fraDato": "2025-01-01", "tilDato": "2025-12-31"})
        risk = summarize(profile([equity]))["attention"]["risks"][0]
        self.assertEqual(risk["kind"], "negative_filed_equity")
        self.assertIn("2025-12-31", risk["text"])
        self.assertIn("does not by itself establish insolvency or bankruptcy", risk["text"])
        self.assertEqual(risk["claim_ids"], ["equity"])
        self.assertEqual(risk["evidence_ids"], ["ev_equity"])

    def test_stale_only_refresh_is_not_reported_as_a_business_change(self):
        row = profile([claim("old_staff", "employees", 4, "workforce", stale=True)],
                      availability={"workforce": {"state": "failed", "reason": "The source did not refresh"}},
                      refresh={"previous_run_id": "previous-run"})
        attention = summarize(row)["attention"]
        self.assertEqual(attention["comparison"], "compared")
        self.assertEqual(attention["changes"], [])
        self.assertEqual(attention["stale"][0]["claim_ids"], ["old_staff"])
        self.assertEqual(attention["stale"][0]["evidence_ids"], ["ev_old_staff"])
        self.assertEqual(attention["unknowns"][0]["state"], "failed")
        self.assertEqual(attention["material_change_count"], 0)

    def test_first_run_and_empty_unknowns_are_explicit_without_claiming_no_risk(self):
        attention = summarize(profile())["attention"]
        self.assertEqual(attention["comparison"], "first_observation")
        self.assertIsNone(attention["previous_run_id"])
        self.assertEqual(attention["changes"], [])
        self.assertIn("do not establish current solvency", attention["risk_note"])
        self.assertEqual(attention["unknowns"], [])

    def test_stale_fact_before_current_replacement_uses_current_summary_but_keeps_stale_record(self):
        row = profile([claim("old_address", "registered_address", {"poststed": "BERGEN"}, "identity", stale=True),
                       claim("new_address", "registered_address", {"poststed": "OSLO"}, "identity")],
                      refresh={"previous_run_id": "previous-run"})
        summary = summarize(row)
        addresses = [sentence["text"] for section in summary["sections"] if section["key"] == "where_it_operates"
                     for sentence in section["sentences"]]
        self.assertTrue(any("OSLO" in sentence for sentence in addresses))
        self.assertFalse(any("BERGEN" in sentence for sentence in addresses))
        self.assertEqual(summary["attention"]["stale"][0]["claim_ids"], ["old_address"])

    def test_stale_negative_equity_is_last_known_not_a_current_risk(self):
        equity = claim("old_equity", "financial_equity", {"amount": "-500", "currency": "NOK"}, "financials",
                       stale=True, period={"fraDato": "2025-01-01", "tilDato": "2025-12-31"})
        attention = summarize(profile([equity], refresh={"previous_run_id": "previous-run"}))["attention"]
        self.assertEqual(attention["risks"], [])
        self.assertEqual(attention["stale"][0]["claim_ids"], ["old_equity"])
        self.assertIn("Last known financial equity", attention["stale"][0]["text"])

    def test_comparison_distinguishes_initial_observation_from_unchanged_refresh(self):
        first = summarize(profile())["attention"]
        refreshed = summarize(profile(refresh={"previous_run_id": "previous-run"}))["attention"]
        self.assertEqual(first["comparison"], "first_observation")
        self.assertEqual(refreshed["comparison"], "compared")
        self.assertIsNone(summarize(profile())["refresh_note"])
        self.assertIn("No material change was confirmed", summarize(profile(refresh={"previous_run_id": "previous-run"}))["refresh_note"])


if __name__ == "__main__":
    unittest.main()
