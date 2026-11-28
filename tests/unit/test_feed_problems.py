"""Error classification and scrubbing for the feed problems view."""
import pytest
import requests

import feed_problems as fp
from rss_parser import RSSParser
from utils.url import SSRFError


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(f'{status} for url: https://h.example.com/x?t=1', response=resp)


@pytest.mark.parametrize('exc,kind', [
    (_http_error(404), 'gone'), (_http_error(410), 'gone'),
    (_http_error(401), 'auth'), (_http_error(403), 'auth'),
    (_http_error(429), 'rate_limited'), (_http_error(503), 'rate_limited'),
    (_http_error(500), 'http_error'), (_http_error(418), 'http_error'),
    (requests.exceptions.SSLError('certificate verify failed'), 'tls'),
    (requests.exceptions.ConnectTimeout('slow'), 'timeout'),
    (requests.exceptions.ReadTimeout('slow'), 'timeout'),
    (requests.exceptions.ConnectionError('NameResolutionError: Failed to resolve'), 'dns'),
    (requests.exceptions.ConnectionError('[Errno -2] Name or service not known'), 'dns'),
    (requests.exceptions.ConnectionError('Connection refused'), 'connection'),
    (SSRFError('private address'), 'blocked_address'),
    (ValueError('weird'), 'unknown'),
])
def test_classify_exception(exc, kind):
    assert fp.classify_exception(exc)[0] == kind


def test_exception_detail_never_carries_a_url():
    detail = fp.classify_exception(_http_error(503))[1]
    assert 'example.com' not in detail and '?t=1' not in detail


@pytest.mark.parametrize('text,kind', [
    ('Fetched feed could not be parsed as RSS (the URL may be returning an error page)', 'invalid_xml'),
    ('Feed body did not parse', 'invalid_xml'),
    ('HTTP 404 Not Found', 'gone'), ('status 503', 'rate_limited'), ('HTTP 403', 'auth'),
    ('Name or service not known', 'dns'), ('SSL certificate problem', 'tls'),
    ('Read timed out', 'timeout'), ('Failed to fetch RSS feed (unreachable)', 'unknown'),
    ('', 'unknown'), (None, 'unknown'),
])
def test_classify_text(text, kind):
    assert fp.classify_text(text) == kind


def test_every_kind_has_a_plain_message_without_dashes():
    for kind in ('dns', 'tls', 'timeout', 'connection', 'gone', 'auth', 'rate_limited', 'http_error',
                 'invalid_xml', 'blocked_address', 'not_feed', 'unknown'):
        message = fp.KIND_MESSAGES[kind]
        assert message and ' - ' not in message and chr(0x2014) not in message


def test_scrub_detail_cuts_urls_to_host_and_redacts_tokens():
    out = fp.scrub_detail('GET https://feeds.example.com/premium/SECRET/rss.xml?key=abc failed')
    assert out == 'GET https://feeds.example.com failed'
    assert fp.scrub_detail('') is None and fp.scrub_detail(None) is None
    assert len(fp.scrub_detail('x' * 1000)) == fp.DETAIL_LIMIT


def test_fetch_failure_detail_keeps_the_legacy_text_without_a_cause():
    assert fp.fetch_failure_detail({}).startswith('Failed to fetch RSS feed (')
    assert fp.fetch_failure_detail({'detail': 'HTTP 503'}) == 'Failed to fetch RSS feed: HTTP 503'


def _channel(xml):
    return RSSParser.find_channel_element(xml)


PC = 'xmlns:podcast="https://podcastindex.org/namespace/1.0" xmlns:sy="http://purl.org/rss/1.0/modules/syndication/"'


@pytest.mark.parametrize('inner,expected', [
    ('<podcast:updateFrequency rrule="FREQ=WEEKLY">weekly</podcast:updateFrequency>',
     {'period_seconds': 604800, 'complete': False}),
    ('<podcast:updateFrequency complete="true">done</podcast:updateFrequency>',
     {'period_seconds': None, 'complete': True}),
    ('<podcast:updateFrequency rrule="garbage">x</podcast:updateFrequency>',
     {'period_seconds': None, 'complete': False}),
    ('<sy:updatePeriod>daily</sy:updatePeriod><sy:updateFrequency>2</sy:updateFrequency>',
     {'period_seconds': 43200, 'complete': False}),
    ('<sy:updatePeriod>fortnightly</sy:updatePeriod>', {'period_seconds': None, 'complete': False}),
    ('<podcast:updateFrequency rrule="FREQ=DAILY"/><sy:updatePeriod>weekly</sy:updatePeriod>',
     {'period_seconds': 86400, 'complete': False}),
    ('<title>x</title>', {'period_seconds': None, 'complete': False}),
])
def test_extract_update_hints(inner, expected):
    xml = f'<rss {PC}><channel>{inner}</channel></rss>'
    assert RSSParser.extract_update_hints(_channel(xml)) == expected


def test_extract_update_hints_without_a_channel():
    assert RSSParser.extract_update_hints(None) == {'period_seconds': None, 'complete': False}
