"""A host that serves an undecodable gzip stream is asked for identity next time."""
import unittest
from unittest.mock import MagicMock, patch

import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('rss_gzip_memo_')
import rss_parser  # noqa: E402


URL = 'https://example.com/rss'


def _ok_response(body=b'<rss/>'):
    response = MagicMock()
    response.status_code = 200
    response.headers = {'Content-Type': 'application/rss+xml',
                        'ETag': '"e1"', 'Last-Modified': None}
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [body]
    return response


class TestGzipIdentityMemo(unittest.TestCase):
    def setUp(self):
        rss_parser._gzip_broken_until.clear()

    def tearDown(self):
        rss_parser._gzip_broken_until.clear()

    def test_first_fetch_asks_for_the_default_encoding(self):
        self.assertNotIn('Accept-Encoding', rss_parser._identity_headers(URL, {}))

    def test_broken_gzip_is_remembered_and_expires(self):
        rss_parser._mark_gzip_broken(URL)
        self.assertEqual(
            rss_parser._identity_headers(URL, {'User-Agent': 'x'}),
            {'User-Agent': 'x', 'Accept-Encoding': 'identity'})

        rss_parser._gzip_broken_until[URL] = 0.0
        self.assertNotIn('Accept-Encoding', rss_parser._identity_headers(URL, {}))
        self.assertNotIn(URL, rss_parser._gzip_broken_until)

    def test_conditional_fetch_marks_then_sends_identity_up_front(self):
        parser = rss_parser.RSSParser()
        decode_error = requests.exceptions.ContentDecodingError('bad gzip')
        with patch('rss_parser.safe_get',
                   side_effect=[decode_error, _ok_response()]) as safe_get, \
             patch('rss_parser.read_response_capped', return_value=b'<rss/>'):
            parser.fetch_feed_conditional(URL)
            first_headers = safe_get.call_args_list[0].kwargs['headers']
            retry_headers = safe_get.call_args_list[1].kwargs['headers']

        self.assertNotIn('Accept-Encoding', first_headers)
        self.assertEqual(retry_headers['Accept-Encoding'], 'identity')

        with patch('rss_parser.safe_get', return_value=_ok_response()) as safe_get, \
             patch('rss_parser.read_response_capped', return_value=b'<rss/>'):
            parser.fetch_feed_conditional(URL)
            next_headers = safe_get.call_args.kwargs['headers']

        self.assertEqual(safe_get.call_count, 1)
        self.assertEqual(next_headers['Accept-Encoding'], 'identity')


if __name__ == '__main__':
    unittest.main()
