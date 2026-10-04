"""Builderr observation export and the standard-questions `answers` block."""
from datetime import datetime
import json
import tempfile
import unittest
from pathlib import Path

from fjordlens.answers import QUESTIONS, build_answers
from fjordlens.observations import label_claims, to_observations, write_observations

ORG = "923609016"
SHA = "a" * 64


def claim(cid, field, family, value, **extra):
    base = {"id": cid, "field": field, "family": family, "value": value, "availability": "available", "stale": False,
            "evidence_ids": ["ev_" + cid], "primary_evidence_id": "ev_" + cid, "source_url": "https://x.no/",
            "retrieved_at": "2026-10-05T10:00:00Z", "content_sha256": SHA, "claim_span": "quote", "source_class": "company_owned",
            "snapshot_path": "snapshots/" + SHA + ".html", "scope": "legal_entity", "reporting_period": None}
    base.update(extra)
    return base


def envelope(claims, **extra):
    env = {"organisation_number": ORG, "legal_identity": {"name": "X AS"}, "claims": claims,
           "evidence": [{"id": c["primary_evidence_id"], "identity_proof": {"method": "identity_gate_v2", "rule": "A: number"},
                         "extraction_method": "html_text_v1"} for c in claims],
           "availability": {"workforce": {"state": "not_available", "reason": "The registry holds no registered employees"},
                            "website": {"state": "ambiguous", "reason": "No candidate passed the exact-company identity gate"}},
           "changes": [], "refresh": {"previous_run_id": None}}
    env.update(extra)
    return env


class ObservationTests(unittest.TestCase):
    def test_external_claims_become_observations_with_provenance(self):
        env = envelope([
            claim("c1", "official_website", "website", "https://x.no/"),
            claim("c2", "company_linked_profile", "social_profiles", {"platform": "twitter", "url": "https://x.com/xas"}),
            claim("c3", "job_posting", "hiring", {"title": "Kokk", "url": "https://arbeidsplassen.nav.no/s/1", "source": "NAV arbeidsplassen.no official job feed", "status": "active"}),
            claim("c4", "company_publication", "activity", {"title": "Ny avtale", "url": "https://x.no/n", "published_on": "2026-09-01"}),
            claim("c5", "employees", "workforce", 12, source_class="official_registry"),
            claim("c6", "legal_name", "identity", "X AS", source_class="official_registry"),
            claim("c7", "company_linked_profile", "social_profiles", {"platform": "myspace", "url": "https://myspace.com/x"}),
            claim("c8", "official_website", "website", "https://old.no/", stale=True),
        ])
        observations = {o["claim_id"]: o for o in to_observations(env)}
        self.assertEqual(set(observations), {"c1", "c2", "c3", "c4", "c5"})
        self.assertEqual((observations["c2"]["platform"], observations["c2"]["signal_type"]), ("x", "profile_handle"))
        self.assertEqual((observations["c3"]["platform"], observations["c3"]["acquisition_mode"]), ("job_board", "official_api"))
        self.assertEqual(observations["c4"]["signal_type"], "public_post")
        self.assertEqual((observations["c5"]["platform"], observations["c5"]["metrics"]), ("brreg", {"employees": 12}))
        for o in observations.values():
            self.assertTrue(o["id"].startswith("obs-"))
            self.assertRegex(o["content_sha256"], r"^[0-9a-f]{64}$")
            self.assertIsNotNone(datetime.fromisoformat(o["retrieved_at"].replace("Z", "+00:00")).tzinfo)
            self.assertTrue(o["exact_entity"] and o["identity_proof"] and o["snapshot_path"])

    def test_label_claims_and_write_round_trip(self):
        env = label_claims(envelope([claim("c1", "official_website", "website", "https://x.no/"), claim("c6", "legal_name", "identity", "X AS")]))
        self.assertEqual(env["claims"][0]["platform"], "company_site")
        self.assertNotIn("platform", env["claims"][1])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "obs.jsonl"
            self.assertEqual(write_observations([env, env], path), 2)
            self.assertEqual(len([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]), 2)


class AnswerTests(unittest.TestCase):
    def test_questions_order_and_unknowns(self):
        answers = build_answers(envelope([]))
        self.assertEqual([a["question"] for a in answers], list(QUESTIONS))
        for a in answers:
            self.assertFalse(a["answerable"])
            self.assertEqual((a["claim_ids"], a["evidence_ids"]), ([], []))
        employees = answers[QUESTIONS.index("How many people does it employ?")]
        self.assertNotIn(" 0 ", " " + employees["answer"] + " ")
        self.assertIn("no registered employees", employees["answer"])
        hiring = answers[QUESTIONS.index("Is it hiring?")]
        self.assertIn("does not show that the company is not hiring", hiring["answer"])

    def test_latest_numbers_use_one_period_and_cite_only_used_claims(self):
        p24 = {"fraDato": "2024-01-01", "tilDato": "2024-12-31"}
        p25 = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
        claims = [claim("r24", "financial_revenue", "financials", {"amount": "1000000.00", "currency": "NOK"}, reporting_period=p24),
                  claim("a25", "financial_assets", "financials", {"amount": "2500000.50", "currency": "NOK"}, reporting_period=p25),
                  claim("n25", "financial_net_profit", "financials", {"amount": "-1234567.00", "currency": "NOK"}, reporting_period=p25)]
        answer = build_answers(envelope(claims))[QUESTIONS.index("What are its latest filed numbers?")]
        self.assertTrue(answer["answerable"])
        self.assertIn("2025-01-01 to 2025-12-31", answer["answer"])
        self.assertIn("annual result NOK -1 234 567", answer["answer"])
        self.assertIn("total assets NOK 2 500 001", answer["answer"])
        self.assertNotIn("1 000 000", answer["answer"])
        self.assertEqual(sorted(answer["claim_ids"]), ["a25", "n25"])

    def test_leaders_cite_only_named_people(self):
        claims = [claim("d1", "registered_role", "leadership", {"name": "Kari", "role_code": "DAGL"}),
                  claim("m1", "registered_role", "leadership", {"name": "Ola", "role_code": "MEDL"})]
        answer = build_answers(envelope(claims))[QUESTIONS.index("Who leads the company?")]
        self.assertEqual((answer["answer"], answer["claim_ids"]), ("Chief executive: Kari.", ["d1"]))

    def test_changes_and_distress(self):
        change = {"type": "closed_job", "field": "job_posting", "material": True, "claim_id": "gone", "current_evidence_ids": [],
                  "previous_evidence_ids": ["ev_old"]}
        env = envelope([claim("b1", "bankrupt", "identity", False), claim("l1", "liquidating", "identity", True)],
                       changes=[change], refresh={"previous_run_id": "r1"})
        answers = build_answers(env)
        changed = answers[QUESTIONS.index("What changed since the last check?")]
        self.assertIn("job ad no longer active", changed["answer"].lower())
        self.assertEqual(changed["evidence_ids"], ["ev_old"])
        distress = answers[QUESTIONS.index("Is it in financial distress?")]
        self.assertEqual((distress["answer"], distress["claim_ids"]), ("The official register flags liquidation.", ["l1"]))
        calm = build_answers(envelope([claim("b1", "bankrupt", "identity", False)]))[QUESTIONS.index("Is it in financial distress?")]
        self.assertEqual(calm["answer"], "The official register does not flag bankruptcy.")

    def test_no_change_after_refresh(self):
        answer = build_answers(envelope([], refresh={"previous_run_id": "r1"}))[QUESTIONS.index("What changed since the last check?")]
        self.assertTrue(answer["answerable"])
        self.assertIn("No material change", answer["answer"])


if __name__ == "__main__":
    unittest.main()
