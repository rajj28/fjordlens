import io
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from fjordlens import net
from fjordlens.net import Budget, FetchError


WEB_SEARCH = "https://api.search.brave.com/res/v1/web/search?q=private-query"
# Both production callers (website nomination and independent-news nomination) use Brave's
# web-search endpoint. Their requests still share the same per-run host cap.
NEWS_SEARCH = "https://api.search.brave.com/res/v1/web/search?q=separate-news-query"


class BraveSearchBudgetTests(unittest.TestCase):
    def test_default_and_environment_configuration(self):
        with mock.patch.object(os, "environ", {}):
            budget = Budget()
            self.assertEqual(budget.max_search_requests, 6)
            self.assertEqual(budget.search_requests, 0)

        with mock.patch.object(os, "environ", {"FJORDLENS_MAX_SEARCH_REQUESTS": "3"}):
            self.assertEqual(Budget().max_search_requests, 3)
        with mock.patch.object(os, "environ", {"FJORDLENS_MAX_SEARCH_REQUESTS": "0"}):
            self.assertEqual(Budget().max_search_requests, 0)

    def test_zero_disables_brave_without_consuming_other_budget(self):
        budget = Budget(requests=10, per_company=10, max_search_requests=0)
        with self.assertRaisesRegex(FetchError, "Brave search request allowance"):
            budget.reserve("123456789", WEB_SEARCH, 0.25)
        self.assertEqual(budget.search_requests, 0)
        self.assertEqual(budget.total, 0)
        self.assertEqual(budget.counts, {})
        self.assertEqual(budget.cost, 0.0)
        self.assertEqual(budget.receipts, [])

    def test_exhausted_cap_rejects_before_dns_connection_or_sleep(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Budget(requests=10, per_company=10, max_search_requests=0)
            fetcher = net.Fetcher(Path(directory), budget, timeout=1)
            with mock.patch.object(net, "resolve_public", side_effect=AssertionError("DNS must not run")) as resolve, \
                    mock.patch.object(net, "PinnedHTTPS", side_effect=AssertionError("connection must not run")) as connect, \
                    mock.patch.object(net.time, "sleep") as sleep:
                response = fetcher.get(WEB_SEARCH, "123456789",
                                       headers={"X-Subscription-Token": "test"}, cost=0.25, store=False)
            self.assertTrue(response.error.startswith("Budget: Brave search request allowance"))
            resolve.assert_not_called()
            connect.assert_not_called()
            sleep.assert_not_called()
            self.assertEqual(budget.search_requests, 0)
            self.assertEqual(budget.total, 0)
            self.assertEqual(budget.cost, 0.0)

    def test_negative_or_malformed_configuration_fails_closed(self):
        for value in ("-1", "", "six", "1.5"):
            with self.subTest(value=value), mock.patch.object(
                    os, "environ", {"FJORDLENS_MAX_SEARCH_REQUESTS": value}):
                with self.assertRaises(ValueError):
                    Budget()
        for value in (-1, True, 1.5):
            with self.subTest(explicit=value), self.assertRaises(ValueError):
                Budget(max_search_requests=value)

    def test_only_exact_brave_hostname_uses_search_cap(self):
        budget = Budget(requests=10, per_company=10, max_search_requests=1)
        for url in (
                "https://api.search.brave.com.attacker.invalid/res/v1/web/search",
                "https://attacker.api.search.brave.com/res/v1/web/search"):
            budget.reserve("123456789", url)
        budget.reserve("123456789", WEB_SEARCH)
        with self.assertRaisesRegex(FetchError, "Brave search request allowance"):
            budget.reserve("123456789", NEWS_SEARCH)
        self.assertEqual(budget.search_requests, 1)
        self.assertEqual(budget.total, 3)

    def test_web_and_news_paths_share_one_cap_and_reservation_counts_each_attempt(self):
        budget = Budget(requests=20, per_company=20, max_search_requests=3)
        budget.reserve("123456789", WEB_SEARCH, 0.1)
        budget.reserve("123456789", WEB_SEARCH, 0.1)  # Retry of the first web request.
        budget.reserve("987654321", NEWS_SEARCH, 0.1)  # News connector, same run budget.
        with self.assertRaises(FetchError):
            budget.reserve("987654321", NEWS_SEARCH, 0.1)  # Redirect/retry attempt is also capped.
        self.assertEqual(budget.search_requests, 3)
        self.assertEqual(budget.total, 3)
        self.assertAlmostEqual(budget.cost, 0.3)
        self.assertEqual(len(budget.receipts), 3)
        self.assertTrue(all("?" not in receipt["url"] for receipt in budget.receipts))

    def test_exhausted_cap_prevents_retry_sleep(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Budget(requests=10, per_company=10, max_search_requests=1)
            fetcher = net.Fetcher(Path(directory), budget, timeout=1)
            fetched = []

            class Connection:
                sock = None

                def __init__(self, host, ip, port, timeout):
                    self.host = host

                def request(self, method, path, headers):
                    fetched.append((self.host, path))

                def getresponse(self):
                    response = io.BytesIO(b"")
                    response.status = 503
                    response.getheaders = lambda: []
                    return response

                def close(self):
                    pass

            with mock.patch.object(net, "resolve_public", return_value="8.8.8.8"), \
                    mock.patch.object(net, "PinnedHTTPS", Connection), \
                    mock.patch.object(net.time, "sleep") as sleep:
                response = fetcher.get(WEB_SEARCH, "123456789",
                                       headers={"X-Subscription-Token": "test"}, cost=0.1, store=False)
            self.assertEqual(response.status, 503)
            self.assertEqual(fetched, [("api.search.brave.com", "/res/v1/web/search?q=private-query")])
            sleep.assert_not_called()
            self.assertEqual(budget.search_requests, 1)
            self.assertEqual(budget.total, 1)
            self.assertAlmostEqual(budget.cost, 0.1)

    def test_concurrent_search_reservations_cannot_exceed_cap(self):
        budget = Budget(requests=100, per_company=100, max_search_requests=7)
        accepted = []
        lock = threading.Lock()

        def reserve(index):
            try:
                budget.reserve(str(index), WEB_SEARCH)
                result = True
            except FetchError:
                result = False
            with lock:
                accepted.append(result)

        with ThreadPoolExecutor(max_workers=32) as pool:
            list(pool.map(reserve, range(80)))
        self.assertEqual(sum(accepted), 7)
        self.assertEqual(budget.search_requests, 7)
        self.assertEqual(budget.total, 7)
        self.assertEqual(len(budget.receipts), 7)


if __name__ == "__main__":
    unittest.main()
