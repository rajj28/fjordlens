"""Bounded recovery exercises real request accounting without external network access."""
import io
import json
import socket
import ssl
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from fjordlens import net


class NetworkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.clock = 1000.0
        self.stack.enter_context(patch.object(net.time, "monotonic", lambda: self.clock))
        self.sleep = self.stack.enter_context(patch.object(net.time, "sleep", side_effect=self.advance))
        self.resolve = self.stack.enter_context(patch.object(net, "resolve_public", return_value="8.8.8.8"))
        self.responses = []
        self.urls = []
        self.timeouts = []
        self.fetcher = net.Fetcher(self.root, net.Budget(50, 300, 50), timeout=2)
        self.fetcher.site_interval = 0
        self.fetcher.official_interval = 0
        owner = self

        class Connection:
            sock = None

            def __init__(self, host, ip, port, timeout):
                self.host = host
                owner.timeouts.append(timeout)

            def request(self, method, path, headers):
                owner.urls.append(self.host + path)

            def getresponse(self):
                if not owner.responses:
                    raise AssertionError("Unexpected HTTP request")
                outcome = owner.responses.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                status, body, headers = outcome
                raw = io.BytesIO(body)
                raw.status = status
                raw.getheaders = lambda: list(headers.items())
                return raw

            def close(self):
                pass

        self.stack.enter_context(patch.object(net, "PinnedHTTP", Connection))
        self.stack.enter_context(patch.object(net, "PinnedHTTPS", Connection))

    def advance(self, seconds):
        self.clock += seconds

    def reply(self, status=200, body=b"", **headers):
        return status, body, headers

    def fetch(self, path="/", org="923609016", **kwargs):
        return self.fetcher.get("https://example.no" + path, org, **kwargs)

    def test_transient_robots_statuses_recover_and_all_requests_are_counted(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.fetcher.robots.clear()
                self.responses = [self.reply(status), self.reply(200), self.reply(200, b"company")]
                before = self.fetcher.budget.total
                result = self.fetch("/status-" + str(status))
                self.assertEqual(result.body, b"company")
                self.assertEqual(self.fetcher.budget.total - before, 3)
                self.assertEqual(self.urls[-3:], ["example.no/robots.txt"] * 2 + ["example.no/status-" + str(status)])
        snapshots = [json.loads(p.read_text()) for p in (self.root / "snapshots").glob("ss_*.json")]
        self.assertTrue(any(p["http_status"] == 503 and p["source_class"] == "access_policy" for p in snapshots))

    def test_socket_timeout_recovers_before_fetching_page(self):
        self.responses = [socket.timeout("timed out"), self.reply(404), self.reply(200, b"company")]
        self.assertEqual(self.fetch().body, b"company")
        self.assertEqual(self.fetcher.budget.total, 3)
        self.assertEqual(self.timeouts, [2, 3, 2])

    def test_redirected_robots_timeout_gets_one_retry_at_that_url(self):
        self.responses = [self.reply(302, location="https://policy.example.no/robots.txt"),
                          socket.timeout("timed out"), self.reply(200), self.reply(200)]
        self.assertEqual(self.fetch().state, "available")
        self.assertEqual(self.urls, ["example.no/robots.txt", "policy.example.no/robots.txt",
                                     "policy.example.no/robots.txt", "example.no/"])
        self.assertEqual(self.fetcher.budget.total, 4)

    def test_retry_allowance_is_shared_across_entire_robots_redirect_chain(self):
        self.responses = [self.reply(503), self.reply(302, location="/policy.txt"), self.reply(503)]
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        self.assertEqual(self.fetcher.budget.total, 3)
        self.sleep.assert_called_once_with(1.5)
        self.assertNotIn("example.no/", self.urls)

    def test_explicit_disallow_is_cached_without_retry_or_page_fetch(self):
        self.responses = [self.reply(200, b"User-agent: *\nDisallow: /\n")]
        for path in ("/", "/contact", "/news"):
            self.assertTrue(self.fetch(path).error.startswith("Robots:"))
        self.advance(100)
        self.assertTrue(self.fetch("/later").error.startswith("Robots:"))
        self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_tls_validation_failure_is_closed_and_never_retried(self):
        self.responses = [ssl.SSLCertVerificationError("certificate verify failed: connection timed out")]
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        self.advance(31)
        self.assertIn("Unreachable: robots.txt", self.fetch("/later").error)
        self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_non_public_dns_is_not_retried_or_counted_as_http(self):
        self.resolve.side_effect = net.FetchError("DNS resolved to a non-public address")
        self.assertIn("non-public", self.fetch().error)
        self.assertEqual(self.fetcher.budget.total, 0)
        self.assertEqual(self.urls, [])
        self.sleep.assert_not_called()

    def test_unsafe_robots_redirect_stops_before_destination(self):
        self.responses = [self.reply(302, location="http://127.0.0.1/private")]
        self.assertIn("unsafe redirect", self.fetch().error)
        self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_robots_redirect_loop_has_fixed_request_bound(self):
        self.responses = [self.reply(302, location="/robots.txt")] * 6
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        self.assertEqual(self.fetcher.budget.total, 6)
        self.sleep.assert_not_called()

    def test_failed_robots_cache_recovers_after_cooldown_without_hammering(self):
        self.responses = [self.reply(503), self.reply(503), self.reply(200), self.reply(200, b"recovered")]
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        for path in ("/contact", "/news", "/jobs"):
            self.assertIn("Unreachable: robots.txt", self.fetch(path).error)
        self.assertEqual(self.fetcher.budget.total, 2)
        self.advance(31)
        self.assertEqual(self.fetch("/after").body, b"recovered")
        self.assertEqual(self.fetcher.budget.total, 4)
        self.assertNotIn("https://example.no", self.fetcher.robots_retry_at)

    def test_long_retry_after_is_respected_by_retry_and_negative_cache(self):
        self.responses = [self.reply(429, **{"retry-after": "3600"})]
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        self.advance(31)
        self.assertIn("Unreachable: robots.txt", self.fetch("/later").error)
        self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_retry_after_applies_to_service_unavailable_too(self):
        self.responses = [self.reply(503, **{"retry-after": "3600"})]
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_http_date_and_nonfinite_retry_after_are_bounded(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True)
        response = net.Response("", "", 429, headers={"retry-after": future})
        self.assertIsNone(self.fetcher._retry_wait(response, False, 0))
        self.assertGreater(self.fetcher._failure_cooldown(response), 3500)
        for value in ("nan", "inf", "-inf", "invalid", "1e999"):
            with self.subTest(value=value):
                response.headers["retry-after"] = value
                self.assertEqual(self.fetcher._retry_wait(response, False, 0), 2.0)

    def test_request_or_company_budget_prevents_retry_and_sleep(self):
        for shared_cap in (True, False):
            with self.subTest(shared_cap=shared_cap):
                self.fetcher.robots.clear()
                self.fetcher.budget = net.Budget(1 if shared_cap else 50, 300, 50 if shared_cap else 1)
                self.responses = [self.reply(503)]
                self.assertIn("Unreachable: robots.txt", self.fetch().error)
                self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_insufficient_wall_time_prevents_retry_and_sleep(self):
        self.fetcher.budget.deadline = self.clock + 3
        self.responses = [socket.timeout("timed out")]
        self.assertIn("Unreachable: robots.txt", self.fetch().error)
        self.assertEqual(self.fetcher.budget.total, 1)
        self.sleep.assert_not_called()

    def test_paid_retry_cannot_sleep_or_exceed_remaining_cost(self):
        self.fetcher.budget.max_cost = 0.25
        self.responses = [self.reply(404), self.reply(503)]
        self.assertEqual(self.fetch(cost=0.25).status, 503)
        self.assertEqual(self.fetcher.budget.cost, 0.25)
        self.assertEqual(self.fetcher.budget.total, 2)
        self.sleep.assert_not_called()

    def test_regular_page_transient_cache_expires(self):
        self.responses = [self.reply(404), self.reply(503), self.reply(503), self.reply(200, b"recovered")]
        self.assertEqual(self.fetch().status, 503)
        self.assertEqual(self.fetch().status, 503)
        self.assertEqual(self.fetcher.budget.total, 3)
        self.advance(31)
        self.assertEqual(self.fetch().body, b"recovered")
        self.assertEqual(self.fetcher.budget.total, 4)

    def test_replay_never_triggers_recovery_network_requests(self):
        saved = net.Response("https://example.no/", "https://example.no/", 503)
        self.fetcher._save(saved)
        replay = net.Fetcher(self.root, net.Budget(), replay=self.root)
        self.assertEqual(replay.get(saved.url, "923609016").status, 503)
        self.assertTrue(replay.get("https://missing.example.no/", "923609016").error.startswith("Replay:"))
        self.assertEqual(replay.budget.total, 0)
        self.assertEqual(self.urls, [])
        self.sleep.assert_not_called()

    def test_third_party_response_and_cache_metadata_do_not_alias(self):
        self.responses = [self.reply(404), self.reply(200, b"article", **{"content-type": "text/html"})]
        first = self.fetch()
        article = self.fetch(source_class="third_party_publication")
        self.assertEqual(first.source_class, "company_owned")
        self.assertEqual(article.source_class, "third_party_publication")
        self.assertEqual(first.snapshot_id, article.snapshot_id)
        article.headers["content-type"] = "changed"
        article.redirects.append({"url": "https://unrelated.no"})
        owned = self.fetch()
        self.assertEqual(owned.source_class, "company_owned")
        self.assertEqual(owned.headers["content-type"], "text/html")
        self.assertEqual(owned.redirects, [])
        self.assertEqual(self.fetcher.budget.total, 2)

    def test_fresh_source_class_and_replay_override_preserve_acquisition_metadata(self):
        self.responses = [self.reply(404), self.reply(200, b"article")]
        article = self.fetch(source_class="third_party_publication")
        metadata_path = self.root / "snapshots" / (article.snapshot_id + ".json")
        acquisition = metadata_path.read_bytes()
        self.assertEqual(json.loads(acquisition)["source_class"], "third_party_publication")
        replay = net.Fetcher(self.root, net.Budget(), replay=self.root)
        owned = replay.get(article.url, "923609016", source_class="company_owned")
        self.assertEqual(owned.source_class, "company_owned")
        self.assertEqual(owned.snapshot_id, article.snapshot_id)
        self.assertEqual(metadata_path.read_bytes(), acquisition)
        self.assertEqual(replay.budget.total, 0)

    def test_invalid_source_classes_cannot_spoof_official_evidence(self):
        for requested, official in (("official_registry", False), ("licensed_news_api", False),
                                    ("third_party_publication", True), ("company_owned", True)):
            with self.subTest(requested=requested, official=official):
                result = self.fetch(source_class=requested, official=official)
                self.assertTrue(result.error.startswith("Policy:"))
                if not official:
                    self.assertEqual(result.source_class, "company_owned")
        self.assertEqual(self.fetcher.budget.total, 0)
        self.assertEqual(self.urls, [])

    def test_allowed_nonofficial_classes_remain_distinct_on_shared_cache(self):
        self.responses = [self.reply(404), self.reply(200, b"shared")]
        for source_class in ("public_job_feed", "third_party_publication", "company_owned"):
            result = self.fetch(source_class=source_class)
            self.assertEqual(result.source_class, source_class)
            self.assertEqual(result.body, b"shared")
        self.assertEqual(self.fetcher.budget.total, 2)

    def test_official_flag_requires_official_host_before_cache_or_network(self):
        result = self.fetch(official=True)
        self.assertIn("outside its trusted host", result.error)
        self.assertEqual(self.fetcher.budget.total, 0)
        self.assertEqual(self.urls, [])


if __name__ == "__main__":
    unittest.main()
