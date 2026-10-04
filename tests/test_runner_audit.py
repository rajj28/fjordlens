import contextlib
import gzip
import io
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fjordlens import official
from fjordlens.core import Profile, digest
from fjordlens.net import Budget, Fetcher, Response
from fjordlens.refresh import refresh
from fjordlens.runner import read_previous, run_batch, snapshot_path


ORG = "923609016"
RETRIEVED = "2026-09-22T00:00:00Z"
URL = official.BASE + "/enheter/" + ORG


class RunnerAuditTests(unittest.TestCase):
    def previous_profile(self, folder, employees=12, *, compressed=False):
        folder.mkdir(parents=True, exist_ok=True)
        fetcher = Fetcher(folder, Budget())
        response = Response(URL, URL, 200, json.dumps({"antallAnsatte": employees}).encode(),
                            {"content-type": "application/json", "etag": '"v1"'}, RETRIEVED)
        fetcher._save(response)
        profile = Profile(ORG, "previous")
        profile.add("employees", employees, response, family="identity", pointer="/antallAnsatte")
        profile.data["run"]["completed_at"] = RETRIEVED
        refresh(profile.data)
        path = self.write_previous(folder, profile.data, compressed=compressed)
        return path, profile.data, response

    def write_previous(self, folder, profile, *, compressed=False):
        path = folder / ("envelopes.jsonl.gz" if compressed else "envelopes.jsonl")
        raw = (json.dumps(profile) + "\n").encode()
        path.write_bytes(gzip.compress(raw) if compressed else raw)
        return path

    def run_offline(self, folder, previous=None, *, identity=None, inputs=None, **kwargs):
        manifest = folder / "input.jsonl"
        values = inputs or [ORG]
        manifest.write_text("".join(json.dumps({"organisation_number": org}) + "\n" for org in values))
        output = folder / "current"
        with contextlib.redirect_stdout(io.StringIO()), \
                patch("fjordlens.runner.official.identity", side_effect=identity or (lambda *_: None)) as connector, \
                patch("fjordlens.net.Fetcher._raw", side_effect=AssertionError("Unexpected network request")):
            report = run_batch(manifest, output, previous=previous, run_id="current", workers=1, **kwargs)
        rows = [json.loads(line) for line in (output / "envelopes.jsonl").read_text().splitlines()]
        return output, rows, report, connector

    def test_failed_connector_retains_previous_bytes_and_replayable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, response = self.previous_profile(root / "previous", compressed=True)
            output, rows, report, _ = self.run_offline(root, previous)
            current = rows[0]
            snapshot = current["source_snapshots"][0]
            self.assertTrue(current["claims"][0]["stale"])
            self.assertEqual(current["changes"], [])
            self.assertEqual(current["evidence"], old["evidence"])
            self.assertEqual((output / snapshot["storage_path"]).read_bytes(), response.body)
            self.assertEqual(snapshot["audit_availability"]["state"], "available")
            self.assertEqual(report["failed_envelopes"], 0)
            self.assertEqual(report["requests"], 0)
            metadata = json.loads((output / "snapshots" / (snapshot["id"] + ".json")).read_text())
            self.assertEqual(metadata["headers"]["etag"], '"v1"')
            replay = Fetcher(output, Budget(), replay=output)
            self.assertEqual(replay.get(URL, ORG).json(), {"antallAnsatte": 12})
            self.assertEqual(replay.budget.total, 0)

    def test_missing_snapshot_metadata_is_reconstructed_from_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, _ = self.previous_profile(root / "previous")
            sid = old["source_snapshots"][0]["id"]
            (previous.parent / "snapshots" / (sid + ".json")).unlink()
            output, rows, report, _ = self.run_offline(root, previous)
            metadata = json.loads((output / "snapshots" / (sid + ".json")).read_text())
            self.assertEqual(metadata["headers"], {"content-type": "application/json", "etag": '"v1"'})
            self.assertEqual(report["failed_envelopes"], 0)
            self.assertEqual(rows[0]["source_snapshots"][0]["audit_availability"]["state"], "available")

    def test_missing_body_keeps_stale_fact_and_fails_audit_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, _ = self.previous_profile(root / "previous")
            (previous.parent / old["source_snapshots"][0]["storage_path"]).unlink()
            output, rows, report, _ = self.run_offline(root, previous)
            current = rows[0]
            snapshot = current["source_snapshots"][0]
            self.assertEqual(current["claims"][0]["value"], 12)
            self.assertTrue(current["claims"][0]["stale"])
            self.assertEqual(current["changes"], [])
            self.assertIsNone(snapshot["storage_path"])
            self.assertEqual(snapshot["audit_availability"]["state"], "unavailable")
            self.assertIn("missing", snapshot["audit_availability"]["reason"])
            self.assertEqual(current["errors"][-1]["family"], "evidence_storage")
            self.assertEqual(report["failed_envelopes"], 1)
            self.assertFalse(list((output / "snapshots").glob("*.bin.gz")))

    def test_corrupt_body_is_never_copied_or_certified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, _ = self.previous_profile(root / "previous")
            (previous.parent / old["source_snapshots"][0]["storage_path"]).write_bytes(gzip.compress(b"altered"))
            output, rows, report, _ = self.run_offline(root, previous)
            snapshot = rows[0]["source_snapshots"][0]
            self.assertIsNone(snapshot["storage_path"])
            self.assertIn("SHA-256", snapshot["audit_availability"]["reason"])
            self.assertEqual(report["failed_envelopes"], 1)
            self.assertFalse(list((output / "snapshots").glob("*.bin.gz")))

    def test_traversal_in_previous_body_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, response = self.previous_profile(root / "previous")
            outside = root / "outside.bin.gz"
            outside.write_bytes(gzip.compress(response.body))
            old["source_snapshots"][0]["storage_path"] = "../outside.bin.gz"
            self.write_previous(previous.parent, old)
            output, rows, report, _ = self.run_offline(root, previous)
            snapshot = rows[0]["source_snapshots"][0]
            self.assertIsNone(snapshot["storage_path"])
            self.assertIn("safe relative", snapshot["audit_availability"]["reason"])
            self.assertEqual(report["failed_envelopes"], 1)
            self.assertFalse(list((output / "snapshots").glob("*.bin.gz")))
            self.assertEqual(gzip.decompress(outside.read_bytes()), response.body)

    def test_paths_reject_windows_drives_unc_and_parent_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            for unsafe in ("../outside", "/absolute", "C:/absolute", "C:relative", "\\\\host\\share",
                           "snapshots/../outside", "snapshots\\file", "snapshots/file:stream", "snapshots//file"):
                with self.subTest(path=unsafe), self.assertRaises(ValueError):
                    snapshot_path(directory, unsafe)

    def test_metadata_only_failed_snapshot_is_not_a_missing_claim_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, _ = self.previous_profile(root / "previous")
            old["source_snapshots"].append(Response(URL + "/failure", URL + "/failure", error="Timeout").metadata())
            self.write_previous(previous.parent, old)
            _, rows, report, _ = self.run_offline(root, previous)
            failed = rows[0]["source_snapshots"][-1]
            self.assertEqual(failed["audit_availability"]["state"], "metadata_only")
            self.assertEqual(report["failed_envelopes"], 0)

    def test_changed_claim_retains_previous_side_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, _ = self.previous_profile(root / "previous")

            def identity(profile, fetcher):
                response = Response(URL, URL, 200, b'{"antallAnsatte":13}', {"content-type": "application/json"},
                                    "2026-09-22T01:00:00Z")
                fetcher._save(response)
                profile.add("employees", 13, response, family="identity", pointer="/antallAnsatte")

            output, rows, report, _ = self.run_offline(root, previous, identity=identity)
            current = rows[0]
            self.assertEqual(len(current["changes"]), 1)
            self.assertEqual(current["changes"][0]["previous_evidence_ids"], old["claims"][0]["evidence_ids"])
            self.assertEqual(len([f for f in (output / "snapshots").iterdir() if re.fullmatch(r"[0-9a-f]{64}\.[a-z]+", f.name)]), 2)
            for snapshot in current["source_snapshots"]:
                self.assertEqual(digest((output / snapshot["storage_path"]).read_bytes()),
                                 snapshot["content_sha256"])
            self.assertEqual(report["failed_envelopes"], 0)

    def test_missing_previous_side_of_change_still_fails_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, old, _ = self.previous_profile(root / "previous")
            (previous.parent / old["source_snapshots"][0]["storage_path"]).unlink()

            def identity(profile, fetcher):
                response = Response(URL, URL, 200, b'{"antallAnsatte":13}', {}, "2026-09-22T01:00:00Z")
                fetcher._save(response)
                profile.add("employees", 13, response, family="identity", pointer="/antallAnsatte")

            _, rows, report, _ = self.run_offline(root, previous, identity=identity)
            self.assertEqual(rows[0]["claims"][0]["value"], 13)
            self.assertEqual(rows[0]["changes"][0]["previous_value"], 12)
            self.assertEqual(report["failed_envelopes"], 1)

    def test_cutoff_rejects_future_previous_evidence_before_connector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, _, _ = self.previous_profile(root / "previous")
            _, rows, report, connector = self.run_offline(root, previous, cutoff="2026-09-22T01:00:00+02:00")
            connector.assert_not_called()
            self.assertEqual(report["requests"], 0)
            self.assertEqual(report["failed_envelopes"], 1)
            self.assertIn("after the supplied cutoff", rows[0]["errors"][0]["message"])

    def test_cutoff_accepts_prior_evidence_using_utc_instants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, _, _ = self.previous_profile(root / "previous")
            _, rows, report, connector = self.run_offline(root, previous, cutoff="2026-09-21T23:30:00-01:00")
            connector.assert_called_once()
            self.assertTrue(rows[0]["claims"][0]["stale"])
            self.assertEqual(report["failed_envelopes"], 0)

    def test_invalid_input_never_becomes_a_checkpoint_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = ["../escape", "C:\\outside\\file", "folder/name", "invalid" * 200, ORG]
            output, rows, report, connector = self.run_offline(root, inputs=inputs)
            self.assertEqual([row["organisation_number"] for row in rows], inputs)
            self.assertEqual(report["terminal_envelopes"], len(inputs))
            self.assertEqual(report["failed_envelopes"], len(inputs) - 1)
            self.assertEqual(sorted(path.name for path in (output / "profiles").iterdir()),
                             ["0000-invalid.json", "0001-invalid.json", "0002-invalid.json", "0003-invalid.json", f"0004-{ORG}.json"])
            connector.assert_called_once()

    def test_gzip_previous_reader_accepts_bom_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous.jsonl.gz"
            path.write_bytes(gzip.compress(('\ufeff\n' + json.dumps({"organisation_number": ORG}) + '\n\n').encode()))
            self.assertEqual(read_previous(path), {ORG: {"organisation_number": ORG}})


if __name__ == "__main__":
    unittest.main()
