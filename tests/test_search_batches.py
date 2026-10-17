import os
import unittest
from unittest import mock

from fjordlens import search
from test_search import ENTITY, FakeFetcher


class SearchBatchTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'test-only'}, clear=True)
    def test_rejected_number_candidates_can_be_followed_by_name_search(self):
        fetcher = FakeFetcher([['https://supplier.no/references'], ['https://fjordkraftteknikk.no/contact']])
        batches = search.nomination_batches(fetcher, ENTITY)
        self.assertEqual(next(batches)[0]['origin'], 'search_orgnr')
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(next(batches)[0]['origin'], 'search_name')
        self.assertEqual(len(fetcher.calls), 2)

    @mock.patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'test-only'}, clear=True)
    def test_accepted_first_batch_does_not_spend_on_second_query(self):
        fetcher = FakeFetcher([['https://fjordkraftteknikk.no/legal'], []])
        batches = search.nomination_batches(fetcher, ENTITY)
        first = next(batches)
        self.assertEqual(first[0]['verification_url'], 'https://fjordkraftteknikk.no/legal')
        batches.close()
        self.assertEqual(len(fetcher.calls), 1)

    @mock.patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'test-only'}, clear=True)
    def test_deep_proof_page_can_revisit_previously_ambiguous_domain(self):
        fetcher = FakeFetcher([['https://fjordkraftteknikk.no/', 'https://fjordkraftteknikk.no/personvern']])
        first = next(search.nomination_batches(fetcher, ENTITY, exclude={'fjordkraftteknikk.no'}, allow_verification=True))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['verification_url'], 'https://fjordkraftteknikk.no/personvern')

    @mock.patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'test-only'}, clear=True)
    def test_proof_page_does_not_bypass_restricted_host_filter(self):
        fetcher = FakeFetcher([['https://www.facebook.com/fjordkraftteknikk'], [], []])
        self.assertEqual(list(search.nomination_batches(fetcher, ENTITY, allow_verification=True)), [[], [], []])


if __name__ == '__main__':
    unittest.main()
