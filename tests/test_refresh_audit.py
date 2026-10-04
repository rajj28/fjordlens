import copy
import json
import unittest

from fjordlens import official
from fjordlens.core import Profile, validate
from fjordlens.net import Response
from fjordlens.refresh import refresh


ORG = "923609016"
OBSERVED = "2026-09-22T00:00:00Z"


class SavedRoles:
    def __init__(self, data, observed=OBSERVED):
        url = official.BASE + "/enheter/" + ORG + "/roller"
        self.response = Response(url, url, 200, json.dumps(data).encode(),
                                 {"content-type": "application/json"}, observed)

    def get(self, *args, **kwargs):
        return self.response


def active_role(name="Original Leader"):
    return {"person": {"navn": {"fornavn": name}},
            "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
            "avregistrert": False}


def roles_profile(data, run_id="previous", observed=OBSERVED):
    profile = Profile(ORG, run_id)
    official.leadership(profile, SavedRoles(data, observed))
    profile.data["run"]["completed_at"] = observed
    return refresh(profile.data)


class RolesRefreshAuditTests(unittest.TestCase):
    def previous(self):
        return roles_profile({"rollegrupper": [{"roller": [active_role()]}]})

    def assert_preserved(self, payload):
        previous = self.previous()
        current = roles_profile(payload, "current")
        self.assertEqual(current["availability"]["leadership"]["state"], "failed")
        self.assertFalse(current["availability"]["leadership"].get("complete"))
        self.assertEqual(current["claims"], [])
        result = refresh(current, previous)
        self.assertEqual(result["changes"], [])
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(result["claims"][0]["value"], previous["claims"][0]["value"])
        self.assertTrue(result["claims"][0]["stale"])
        self.assertEqual(validate(result), [])

    def test_malformed_nested_lists_do_not_remove_prior_roles(self):
        for payload in ({"rollegrupper": [{}]}, {"rollegrupper": [None]},
                        {"rollegrupper": [{"roller": {}}]},
                        {"rollegrupper": [{"roller": [None]}]}):
            with self.subTest(payload=payload):
                self.assert_preserved(payload)

    def test_unidentifiable_active_role_does_not_remove_prior_roles(self):
        missing_name = active_role()
        missing_name.pop("person")
        missing_code = active_role()
        missing_code["type"].pop("kode")
        malformed_status = {**active_role(), "avregistrert": "false"}
        for role in (missing_name, missing_code, malformed_status):
            with self.subTest(role=role):
                self.assert_preserved({"rollegrupper": [{"roller": [role]}]})

    def test_partial_parsing_does_not_publish_a_partial_role_set(self):
        self.assert_preserved({"rollegrupper": [{"roller": [active_role("New Leader"), {}]}]})

    def test_explicit_empty_and_inactive_sets_still_establish_removal(self):
        for payload in ({"rollegrupper": []}, {"rollegrupper": [{"roller": []}]},
                        {"rollegrupper": [{"roller": [{"avregistrert": True}]}]}):
            with self.subTest(payload=payload):
                current = roles_profile(payload, "current")
                self.assertTrue(current["availability"]["leadership"]["complete"])
                result = refresh(current, self.previous())
                self.assertEqual(result["claims"], [])
                self.assertEqual([c["type"] for c in result["changes"]], ["removed_role"])

    def test_corporate_role_names_remain_supported(self):
        corporate = {"enhet": {"navn": ["Audit", "AS"], "organisasjonsnummer": ORG},
                     "type": {"kode": "REVI", "beskrivelse": "Revisor"}}
        result = roles_profile({"rollegrupper": [{"roller": [corporate]}]})
        self.assertEqual(result["claims"][0]["value"]["name"], "Audit AS")
        self.assertTrue(result["availability"]["leadership"]["complete"])


class CutoffRefreshAuditTests(unittest.TestCase):
    def previous(self, observed=OBSERVED):
        return roles_profile({"rollegrupper": [{"roller": [active_role()]}]}, observed=observed)

    def current(self):
        profile = Profile(ORG, "current")
        profile.state("leadership", "blocked", "Cutoff prevents live retrieval")
        profile.data["run"]["completed_at"] = OBSERVED
        return profile.data

    def test_future_previous_evidence_is_rejected_before_mutation(self):
        current = self.current()
        unchanged = copy.deepcopy(current)
        with self.assertRaisesRegex(ValueError, "after the supplied cutoff"):
            refresh(current, self.previous(), cutoff="2025-01-01T00:00:00Z")
        self.assertEqual(current, unchanged)

    def test_cutoff_and_evidence_offsets_are_compared_as_instants(self):
        earlier = self.previous("2026-09-22T01:00:00+02:00")
        result = refresh(self.current(), earlier, cutoff="2026-09-22T00:00:00Z")
        self.assertEqual(len(result["claims"]), 1)
        self.assertTrue(result["claims"][0]["stale"])
        with self.assertRaisesRegex(ValueError, "after the supplied cutoff"):
            refresh(self.current(), self.previous("2026-09-21T23:30:00Z"),
                    cutoff="2026-09-22T01:00:00+02:00")

    def test_naive_and_invalid_cutoffs_are_rejected(self):
        for cutoff in ("2026-09-22T00:00:00", "2026-09-22", "invalid"):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                refresh(self.current(), self.previous(), cutoff=cutoff)

    def test_retained_successful_snapshot_also_obeys_cutoff(self):
        previous = self.previous("2025-01-01T00:00:00Z")
        previous["source_snapshots"].append({"http_status": 200, "retrieved_at": OBSERVED})
        with self.assertRaisesRegex(ValueError, "after the supplied cutoff"):
            refresh(self.current(), previous, cutoff="2025-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
