"""fetch_feed and fetch_feed_conditional share one capped-body read."""
from unittest.mock import MagicMock

import pytest

import rss_parser

URL = 'https://example.com/feed.xml'


@pytest.fixture
def parser(monkeypatch):
    response = MagicMock(status_code=200)
    response.headers = {'Content-Type': 'application/rss+xml', 'ETag': 'e1',
                        'Last-Modified': 'lm1'}
    breaker = MagicMock()
    monkeypatch.setattr(rss_parser, 'safe_get', lambda *a, **k: response)
    monkeypatch.setattr(rss_parser, '_get_rss_circuit_breaker', lambda url: breaker)
    return rss_parser.RSSParser(), response, breaker


def test_body_is_decoded_and_the_response_closed(parser, monkeypatch):
    p, response, breaker = parser
    monkeypatch.setattr(rss_parser, 'read_response_capped', lambda r, cap: b'<rss/>')
    assert p.fetch_feed(URL) == '<rss/>'
    assert p.fetch_feed_conditional(URL) == ('<rss/>', 'e1', 'lm1')
    assert response.close.called
    assert breaker.record_success.call_count == 2
    breaker.record_failure.assert_not_called()


@pytest.mark.parametrize('error', [rss_parser.ResponseTooLargeError('big'), rss_parser.IncompleteResponseError('cut')])
def test_oversize_or_truncated_body_fails_the_breaker(parser, monkeypatch, error):
    p, response, breaker = parser

    def raiser(r, cap):
        raise error

    monkeypatch.setattr(rss_parser, 'read_response_capped', raiser)
    assert p.fetch_feed(URL) is None
    assert p.fetch_feed_conditional(URL) == (None, None, None)
    assert breaker.record_failure.call_count == 2
    breaker.record_success.assert_not_called()
    assert response.close.called
