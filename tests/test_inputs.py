import json
from pathlib import Path
import tempfile
import unittest

from fjordlens.runner import read_inputs

ORG, OTHER = "923609016", "990888213"


class InputParsingTests(unittest.TestCase):
    def parse(self, text, name="input.txt"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(text, encoding="utf-8")
            return read_inputs(path)

    def test_quoted_csv_header_and_rows(self):
        text = '"organisation_number","name"\n"923609016","Example AS"\n"990888213","Other AS"\n'
        self.assertEqual(self.parse(text, "input.csv"), [ORG, OTHER])

    def test_single_quoted_header_is_not_a_company(self):
        self.assertEqual(self.parse('"organisation_number"\n"923609016"\n'), [ORG])

    def test_jsonl_objects_and_strings(self):
        text = json.dumps({"organisation_number": ORG}) + "\n" + json.dumps(OTHER) + "\n"
        self.assertEqual(self.parse(text, "input.jsonl"), [ORG, OTHER])

    def test_json_array_and_object_forms(self):
        self.assertEqual(self.parse(json.dumps([ORG, {"orgnr": OTHER}]), "input.json"), [ORG, OTHER])
        self.assertEqual(self.parse(json.dumps({"organisation_numbers": [ORG]}), "input.json"), [ORG])

    def test_plain_text_with_spacing_and_semicolon_csv(self):
        self.assertEqual(self.parse("923 609 016\n990888213\n"), [ORG, OTHER])
        self.assertEqual(self.parse("orgnr;navn\n923609016;Example\n"), [ORG])

    def test_malformed_rows_never_raise_and_keep_their_position(self):
        rows = self.parse('{"organisation_number": broken\n923609016\nnot-a-number\n')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1], ORG)

    def test_tsv_header_with_other_columns_first(self):
        self.assertEqual(self.parse("name\torganisasjonsnummer\nExample\t923609016\n", "input.tsv"), [ORG])


if __name__ == "__main__":
    unittest.main()
