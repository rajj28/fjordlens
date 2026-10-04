import json
import random
import unittest

from fjordlens.jsonspan import index, locate, quote

SAMPLE = ('{"organisasjonsnummer":"923609016","navn":"EQUINOR ASA","organisasjonsform":{"kode":"ASA","beskrivelse":"Allmennaksjeselskap"},'
          '"naeringskode1":{"kode":"06.100","beskrivelse":"Utvinning av råolje"},"antallAnsatte":21272,"konkurs":false,'
          '"egenkapital":{"sumEgenkapital":39182000000.00}}')


def walk(value, pointer=""):
    yield pointer, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, pointer + f"/{i}")


class JsonSpanTests(unittest.TestCase):
    def test_sample_quotes_are_verbatim(self):
        self.assertEqual(quote(SAMPLE, "/navn"), '"navn":"EQUINOR ASA"')
        self.assertEqual(quote(SAMPLE, "/naeringskode1/beskrivelse", with_key=False), '"Utvinning av råolje"')
        self.assertEqual(quote(SAMPLE, "/egenkapital/sumEgenkapital"), '"sumEgenkapital":39182000000.00')
        self.assertIsNone(locate(SAMPLE, "/missing"))

    def test_random_documents_round_trip(self):
        rnd = random.Random(1234)
        def gen(depth=0):
            if depth > 4 or rnd.random() < 0.4:
                return rnd.choice([None, True, False, rnd.randint(-999, 999), rnd.random() * 1e6, "".join(rnd.choice('aø"\\/\n€ {}[]:,') for _ in range(rnd.randint(0, 8)))])
            if rnd.random() < 0.5:
                return [gen(depth + 1) for _ in range(rnd.randint(0, 4))]
            return {"k~/" [rnd.randint(0, 2)] + str(i): gen(depth + 1) for i in range(rnd.randint(0, 4))}
        for _ in range(200):
            document = gen()
            for text in (json.dumps(document, ensure_ascii=False, separators=(",", ":")), json.dumps(document, indent=2), json.dumps(document)):
                spans, expected = index(text), dict(walk(json.loads(text)))
                self.assertEqual(set(spans), set(expected))
                for pointer, (member, start, end) in spans.items():
                    self.assertEqual(json.loads(text[start:end]), expected[pointer])
                    self.assertLessEqual(member, start)

    def test_duplicate_key_points_to_last_occurrence(self):
        text = '{"a":1,"a":2}'
        member, start, end = locate(text, "/a")
        self.assertEqual(text[start:end], "2")
        self.assertEqual(text[member:end], '"a":2')

    def test_invalid_json_is_rejected(self):
        for bad in ["", "{", "[1,]", '{"a" 1}', "tru", "01", '"\\x"', "[1] x", "[" * 600 + "]" * 600]:
            with self.subTest(bad=bad[:12]), self.assertRaises(ValueError):
                index(bad)

    def test_quote_falls_back_to_value_then_none(self):
        text = '{"k":"' + "x" * 50 + '"}'
        self.assertEqual(quote(text, "/k", max_len=200), '"k":"' + "x" * 50 + '"')
        self.assertEqual(quote(text, "/k", max_len=53), '"' + "x" * 50 + '"')
        self.assertIsNone(quote(text, "/k", max_len=10))


if __name__ == "__main__":
    unittest.main()
