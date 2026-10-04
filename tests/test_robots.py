import unittest
from fjordlens.robots import Robots


class TestStatusHandling(unittest.TestCase):
    def test_200_empty_body_allows(self):
        r = Robots.from_response(200, b"")
        self.assertEqual(r.state, "parsed")
        self.assertTrue(r.allowed("bot", "/"))

    def test_404_allows_unavailable(self):
        r = Robots.from_response(404, b"")
        self.assertEqual(r.state, "unavailable")
        self.assertTrue(r.allowed("bot", "/"))

    def test_403_allows_unavailable(self):
        r = Robots.from_response(403, b"")
        self.assertEqual(r.state, "unavailable")
        self.assertTrue(r.allowed("bot", "/"))

    def test_410_allows_unavailable(self):
        r = Robots.from_response(410, b"")
        self.assertEqual(r.state, "unavailable")
        self.assertTrue(r.allowed("bot", "/"))

    def test_429_disallows_unreachable(self):
        r = Robots.from_response(429, b"")
        self.assertEqual(r.state, "unreachable")
        self.assertFalse(r.allowed("bot", "/"))

    def test_500_disallows_unreachable(self):
        r = Robots.from_response(500, b"")
        self.assertEqual(r.state, "unreachable")
        self.assertFalse(r.allowed("bot", "/"))

    def test_503_disallows_unreachable(self):
        r = Robots.from_response(503, b"")
        self.assertEqual(r.state, "unreachable")
        self.assertFalse(r.allowed("bot", "/"))

    def test_error_true_disallows(self):
        r = Robots.from_response(200, b"", error=True)
        self.assertEqual(r.state, "unreachable")
        self.assertFalse(r.allowed("bot", "/"))

    def test_none_status_disallows(self):
        r = Robots.from_response(None, b"")
        self.assertEqual(r.state, "unreachable")
        self.assertFalse(r.allowed("bot", "/"))


class TestHTMLBody(unittest.TestCase):
    def test_html_body_allows_all(self):
        html = b"<html><body>Not found</body></html>"
        r = Robots.from_response(200, html)
        self.assertEqual(r.state, "parsed")
        self.assertTrue(r.allowed("bot", "/anything"))


class TestBasicDisallow(unittest.TestCase):
    def test_disallow_private(self):
        body = b"User-agent: *\nDisallow: /private"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/private"))
        self.assertFalse(r.allowed("bot", "/private/x"))
        self.assertFalse(r.allowed("bot", "/privateer"))
        self.assertTrue(r.allowed("bot", "/public"))


class TestLongestMatch(unittest.TestCase):
    def test_longest_match_wins(self):
        body = b"User-agent: *\nDisallow: /a\nAllow: /a/b"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/a/c"))
        self.assertTrue(r.allowed("bot", "/a/b/c"))

    def test_equal_length_allow_wins(self):
        body = b"User-agent: *\nAllow: /x\nDisallow: /x"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("bot", "/x"))


class TestWildcards(unittest.TestCase):
    def test_wildcard_pdf_anchor(self):
        body = b"User-agent: *\nDisallow: /*.pdf$"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/docs/file.pdf"))
        self.assertTrue(r.allowed("bot", "/docs/file.pdf?x=1"))
        self.assertTrue(r.allowed("bot", "/docs/file.pdfx"))

    def test_wildcard_query(self):
        body = b"User-agent: *\nDisallow: /*?"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/search?q=1"))
        self.assertTrue(r.allowed("bot", "/search"))


class TestSpecificGroupBeatsStar(unittest.TestCase):
    def test_specific_group_beats_star(self):
        body = b"User-agent: *\nDisallow: /\n\nUser-agent: FjordLens\nAllow: /"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("FjordLens", "/x"))
        self.assertFalse(r.allowed("OtherBot", "/x"))

    def test_case_insensitive_token_matching(self):
        body = b"User-agent: fjordlens/2.0\nAllow: /"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("FjordLens", "/x"))
        self.assertTrue(r.allowed("fjordlens", "/x"))
        self.assertTrue(r.allowed("FJORDLENS", "/x"))


class TestGroupMerging(unittest.TestCase):
    def test_multiple_star_groups_merged(self):
        body = b"User-agent: *\nDisallow: /a\n\nUser-agent: *\nDisallow: /b"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/a"))
        self.assertFalse(r.allowed("bot", "/b"))
        self.assertTrue(r.allowed("bot", "/c"))


class TestMultipleUserAgentLines(unittest.TestCase):
    def test_multiple_ua_lines_one_group(self):
        body = b"User-agent: a\nUser-agent: b\nDisallow: /z"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("a", "/z"))
        self.assertFalse(r.allowed("b", "/z"))
        self.assertTrue(r.allowed("c", "/z"))


class TestCommentsAndFormatting(unittest.TestCase):
    def test_comments_crlf_bom_mixed_case_spaces(self):
        body = "\ufeff# comment\r\nUSER-AGENT : *\r\nDISALLOW : /private\r\n# another\r\nALLOW : /public".encode("utf-8")
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/private"))
        self.assertTrue(r.allowed("bot", "/public"))


class TestEmptyDisallow(unittest.TestCase):
    def test_empty_disallow_allows_all(self):
        body = b"User-agent: *\nDisallow:"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("bot", "/anything"))


class TestCrawlDelay(unittest.TestCase):
    def test_crawl_delay_parsed(self):
        body = b"User-agent: *\nCrawl-delay: 10.5"
        r = Robots.from_response(200, body)
        self.assertEqual(r.crawl_delay("bot"), 10.5)

    def test_invalid_crawl_delay_none(self):
        body = b"User-agent: *\nCrawl-delay: invalid"
        r = Robots.from_response(200, body)
        self.assertIsNone(r.crawl_delay("bot"))


class TestRobotsTxtAlwaysAllowed(unittest.TestCase):
    def test_robots_txt_always_allowed(self):
        body = b"User-agent: *\nDisallow: /"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("bot", "/robots.txt"))
        self.assertFalse(r.allowed("bot", "/other"))


class TestFullUrlAndPathInput(unittest.TestCase):
    def test_full_url_and_both_work(self):
        body = b"User-agent: *\nDisallow: /private"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "https://example.com/private"))
        self.assertFalse(r.allowed("bot", "/private"))

    def test_percent_encoding_normalization(self):
        body = b"User-agent: *\nDisallow: /%7Ejoe"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/~joe/x"))
        self.assertFalse(r.allowed("bot", "/%7Ejoe/x"))


class TestAdditionalEdgeCases(unittest.TestCase):
    def test_rules_before_user_agent_ignored(self):
        body = b"Disallow: /ignored\nUser-agent: *\nAllow: /"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("bot", "/ignored"))

    def test_allow_empty_ignored(self):
        body = b"User-agent: *\nAllow:\nDisallow: /private"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/private"))

    def test_crawl_delay_for_specific_group(self):
        body = b"User-agent: *\nCrawl-delay: 5\n\nUser-agent: FjordLens\nCrawl-delay: 2.5"
        r = Robots.from_response(200, body)
        self.assertEqual(r.crawl_delay("OtherBot"), 5)
        self.assertEqual(r.crawl_delay("FjordLens"), 2.5)

    def test_no_matching_group_allows_all(self):
        body = b"User-agent: SpecificBot\nDisallow: /"
        r = Robots.from_response(200, body)
        self.assertTrue(r.allowed("OtherBot", "/anything"))

    def test_url_with_fragment(self):
        body = b"User-agent: *\nDisallow: /private"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "https://example.com/private#section"))

    def test_percent_encoding_uppercase_other(self):
        body = b"User-agent: *\nDisallow: /%41bc"
        r = Robots.from_response(200, body)
        self.assertFalse(r.allowed("bot", "/Abc"))
        self.assertFalse(r.allowed("bot", "/%41bc"))



class RobotsReviewFixes(unittest.TestCase):
    def test_root_url_without_path_is_matched(self):
        r = Robots.from_response(200, b"User-agent: *\nDisallow: /")
        self.assertFalse(r.allowed("FjordLens", "https://example.no"))

    def test_inline_comment_is_not_part_of_rule(self):
        r = Robots.from_response(200, b"User-agent: *\nDisallow: /private # keep out")
        self.assertFalse(r.allowed("FjordLens", "/private/page"))

    def test_html_word_in_comment_does_not_discard_rules(self):
        r = Robots.from_response(200, b"# <html> legacy note\nUser-agent: *\nDisallow: /")
        self.assertFalse(r.allowed("FjordLens", "/x"))

    def test_non_ascii_rule_matches_percent_encoded_url(self):
        r = Robots.from_response(200, "User-agent: *\nDisallow: /om-oss/ø".encode("utf-8"))
        self.assertFalse(r.allowed("FjordLens", "/om-oss/%C3%B8/kontakt"))


if __name__ == "__main__":
    unittest.main()