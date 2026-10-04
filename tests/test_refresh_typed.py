"""Typed refresh changes (Builderr's first evaluation report: missed renames and revenue-free filings)."""
import copy
import unittest

from fjordlens.core import digest
from fjordlens.refresh import refresh

ORG = "923609016"


def claim(field, family, value, *, key="", period=None, effective_at=None, run="2026-10-01T10:00:00Z"):
    cid = "cl_" + digest(["claim-v1", ORG, family, field, key, "legal_entity", period])[:24]
    return {"id": cid, "field": field, "family": family, "key": key, "value": value, "availability": "available",
            "scope": "legal_entity", "evidence_ids": ["ev_" + cid[3:]], "effective_at": effective_at,
            "reporting_period": period, "first_observed_at": run, "last_observed_at": run, "stale": False}


def envelope(claims, *, run_id, completed, availability=None, attempts=()):
    families = ("identity", "financials", "leadership", "locations", "group", "filing_history", "website", "description",
                "contact", "social_profiles", "hiring", "activity", "workforce")
    state = {f: {"state": "available", "reason": "checked", "complete": True} for f in families}
    state.update(availability or {})
    return {"organisation_number": ORG, "run": {"run_id": run_id, "completed_at": completed}, "legal_identity": {"name": "X AS"},
            "claims": copy.deepcopy(claims), "evidence": [], "source_snapshots": [{"id": "ss_1", "http_status": 200, "retrieved_at": completed}],
            "availability": state, "changes": [], "refresh": {"previous_run_id": None, "preserved_evidence": [], "stale_claim_ids": []},
            "attempts": list(attempts)}


def material(result):
    return [c["type"] for c in result["changes"] if c["material"]]


class TypedRefreshTests(unittest.TestCase):
    def test_unchanged_replay_has_no_changes(self):
        claims = [claim("legal_name", "identity", "OLD NAME AS"), claim("latest_filing_year", "identity", "2024")]
        previous = envelope(claims, run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope(claims, run_id="r2", completed="2026-10-02T10:00:00Z")
        self.assertEqual(refresh(current, previous)["changes"], [])

    def test_rename_is_one_changed_name(self):
        previous = envelope([claim("legal_name", "identity", "OLD NAME AS")], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([claim("legal_name", "identity", "NEW NAME AS")], run_id="r2", completed="2026-10-02T10:00:00Z")
        result = refresh(current, previous)
        self.assertEqual(material(result), ["changed_name"])
        self.assertEqual((result["changes"][0]["previous_value"], result["changes"][0]["current_value"]), ("OLD NAME AS", "NEW NAME AS"))

    def test_revenue_free_new_filing_is_exactly_one_new_filing(self):
        p24 = {"fraDato": "2024-01-01", "tilDato": "2024-12-31"}
        p25 = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
        assets = lambda period, amount: claim("financial_assets", "financials", {"amount": amount, "currency": "NOK", "unit": "currency_units"},  # noqa: E731
                                              key=period["fraDato"], period=period)
        previous = envelope([claim("latest_filing_year", "identity", "2024"), claim("available_filing_years", "filing_history", ["2024", "2023"]),
                             assets(p24, "1000")], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([claim("latest_filing_year", "identity", "2025"), claim("available_filing_years", "filing_history", ["2025", "2024", "2023"]),
                            assets(p24, "1000"), assets(p25, "1500")], run_id="r2", completed="2026-10-02T10:00:00Z")
        result = refresh(current, previous)
        self.assertEqual(material(result), ["new_filing"])
        self.assertEqual(result["changes"][0]["filing_years"], ["2025"])
        self.assertEqual(sorted(c["type"] for c in result["changes"] if not c["material"]), ["filing_value"])

    def test_new_period_without_filing_year_claims_still_reports_one_filing(self):
        p24 = {"fraDato": "2024-01-01", "tilDato": "2024-12-31"}
        p25 = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
        fin = lambda field, period: claim(field, "financials", {"amount": "5", "currency": "NOK", "unit": "currency_units"}, key=field + period["fraDato"], period=period)  # noqa: E731
        previous = envelope([fin("financial_assets", p24)], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([fin("financial_assets", p24), fin("financial_assets", p25), fin("financial_debt", p25)],
                           run_id="r2", completed="2026-10-02T10:00:00Z")
        self.assertEqual(material(refresh(current, previous)), ["new_filing"])

    def test_new_role_needs_complete_role_sets_on_both_runs(self):
        role = claim("registered_role", "leadership", {"name": "Kari Nordmann", "role_code": "DAGL"}, key="DAGL:Kari Nordmann")
        previous = envelope([], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([role], run_id="r2", completed="2026-10-02T10:00:00Z")
        self.assertEqual(material(refresh(copy.deepcopy(current), previous)), ["new_role"])
        partial = envelope([], run_id="r1", completed="2026-10-01T10:00:00Z", availability={"leadership": {"state": "failed", "reason": "timeout"}})
        self.assertEqual(material(refresh(copy.deepcopy(current), partial)), [])

    def test_removed_role_from_a_complete_set(self):
        role = claim("registered_role", "leadership", {"name": "Kari Nordmann", "role_code": "DAGL"}, key="DAGL:Kari Nordmann")
        previous = envelope([role], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([], run_id="r2", completed="2026-10-02T10:00:00Z")
        result = refresh(current, previous)
        self.assertEqual(material(result), ["removed_role"])
        self.assertEqual(result["claims"], [])

    def test_job_closed_only_on_nav_evidence_that_it_is_no_longer_active(self):
        job = claim("job_posting", "hiring", {"title": "Kokk", "status": "active", "source": "NAV arbeidsplassen.no official job feed"},
                    key="uuid-1", effective_at="2026-09-20")
        previous = envelope([job], run_id="r1", completed="2026-10-01T10:00:00Z")
        closed = envelope([], run_id="r2", completed="2026-10-02T10:00:00Z",
                          attempts=[{"strategy": "nav_job_feed_v1", "live_listing_complete": True, "confirmed_inactive": ["uuid-1"]}])
        result = refresh(closed, copy.deepcopy(previous))
        self.assertEqual(material(result), ["closed_job"])
        self.assertEqual(result["claims"], [])
        # The feed caught up completely, but this ad's own check failed: no evidence of closure, keep it stale.
        failed = envelope([], run_id="r2", completed="2026-10-02T10:00:00Z",
                          attempts=[{"strategy": "nav_job_feed_v1", "live_listing_complete": True, "confirmed_inactive": [],
                                     "unverified": ["uuid-1"]}])
        result = refresh(failed, copy.deepcopy(previous))
        self.assertEqual(material(result), [])
        self.assertTrue(result["claims"][0]["stale"])

    def test_recovered_figures_of_an_already_known_filing_are_not_a_new_filing(self):
        p24 = {"fraDato": "2024-01-01", "tilDato": "2024-12-31"}
        p25 = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
        years = claim("available_filing_years", "filing_history", ["2025", "2024"])
        latest = claim("latest_filing_year", "filing_history", "2025")
        rev24 = claim("financial_revenue", "financials", {"amount": "10", "currency": "NOK"}, key="2024", period=p24)
        rev25 = claim("financial_revenue", "financials", {"amount": "12", "currency": "NOK"}, key="2025", period=p25)
        previous = envelope([years, latest, rev24], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([years, latest, rev24, rev25], run_id="r2", completed="2026-10-02T10:00:00Z")
        result = refresh(current, copy.deepcopy(previous))
        self.assertEqual(material(result), [])

    def test_new_job_dated_after_previous_run_is_material(self):
        job = claim("job_posting", "hiring", {"title": "Kokk", "status": "active"}, key="uuid-2", effective_at="2026-10-02")
        previous = envelope([], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([job], run_id="r2", completed="2026-10-02T12:00:00Z")
        self.assertEqual(material(refresh(current, previous)), ["new_job"])

    def test_first_observation_of_a_website_is_not_a_change(self):
        site = claim("official_website", "website", "https://x.no/")
        previous = envelope([], run_id="r1", completed="2026-10-01T10:00:00Z")
        current = envelope([site], run_id="r2", completed="2026-10-02T10:00:00Z")
        result = refresh(current, previous)
        self.assertEqual(material(result), [])
        self.assertEqual([c["type"] for c in result["changes"]], ["first_observed"])


if __name__ == "__main__":
    unittest.main()
