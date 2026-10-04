import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fjordlens.net import Budget, Fetcher
from test_core import ORG, ENTITY, response


class SourceBundleTests(unittest.TestCase):
    def test_frozen_identity_does_not_fetch_live(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = Fetcher(root/'prior', Budget())
            r = response(ENTITY)
            saved._save(r)
            current = Fetcher(root/'new', Budget(), registry_snapshot=root/'prior')
            with patch.object(current, '_raw', side_effect=AssertionError('unexpected network')):
                self.assertEqual(current.get(r.url, ORG, official=True).json(), ENTITY)
                self.assertEqual(current.budget.total, 0)
                missing = current.get(r.url.replace(ORG, '990888213'), '990888213', official=True)
                self.assertEqual(missing.state, 'failed')
            self.assertTrue((root/'new'/r.storage_path).exists())

    def test_cutoff_compares_timezones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            r = response(ENTITY)
            Fetcher(root, Budget())._save(r)
            # 2026-09-22 00:00Z is AFTER this cutoff despite a later local date.
            f = Fetcher(root, Budget(), replay=root, cutoff='2026-09-22T00:30:00+01:00')
            self.assertEqual(f.get(r.url, ORG).state, 'failed')
            # The same UTC instant is admissible with a different offset.
            f = Fetcher(root, Budget(), replay=root, cutoff='2026-09-21T20:00:00-04:00')
            self.assertEqual(f.get(r.url, ORG).json(), ENTITY)

    def test_naive_cutoff_rejected(self):
        with self.assertRaises(ValueError):
            Fetcher(Path('unused'), Budget(), cutoff='2026-09-22')


if __name__ == '__main__':
    unittest.main()
