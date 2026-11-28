"""HTTP hints (Retry-After, max-age, error class) fetch_feed_conditional hands the adaptive scheduler."""
import itertools
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import defusedxml
import pytest
import requests

defusedxml.defuse_stdlib()

import feed_schedule  # noqa: E402
from feed_problems import kind_for_status  # noqa: E402
from rss_parser import RSSParser  # noqa: E402
from utils.circuit_breaker import CircuitBreakerOpen  # noqa: E402
from utils.url import SSRFError  # noqa: E402

BODY = b"<rss><channel><title>X</title></channel></rss>"
_hosts = itertools.count()


@pytest.fixture
def url():
    # Fresh host per test: the per-host circuit breaker is process global.
    return f'https://hints{next(_hosts)}.example.com/feed.xml'


def _response(status=200, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {'Content-Type': 'application/rss+xml', **(headers or {})}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    return resp


def _fetch(url, response=None, error=None, etag=None):
    meta = {}
    with patch('rss_parser.safe_get', return_value=response, side_effect=error), \
            patch('rss_parser.read_response_capped', return_value=BODY):
        result = RSSParser().fetch_feed_conditional(url, etag=etag, meta=meta)
    return result, meta


def _http_date(seconds):
    return format_datetime(datetime.now(timezone.utc) + timedelta(seconds=seconds), usegmt=True)


@pytest.mark.parametrize('status', [429, 503])
@pytest.mark.parametrize('header,expected', [
    ('120', 120),
    (300, pytest.approx(300, abs=5)),
    ('99999999999', feed_schedule.RETRY_AFTER_MAX_SECONDS),
    (10 * 86400, feed_schedule.RETRY_AFTER_MAX_SECONDS),
    ('-5', None),
    ('0', None),
    ('soon', None),
    ('', None),
    (-60, None),
])
def test_retry_after_captured_and_bounded(url, status, header, expected):
    if isinstance(header, int):
        header = _http_date(header)
    result, meta = _fetch(url, _response(status, {'Retry-After': header}))
    assert result == (None, None, None)
    assert meta['status'] == status
    assert meta['retry_after'] == header
    assert feed_schedule.retry_after_seconds(meta['retry_after']) == expected


def test_retry_after_absent_on_429(url):
    _, meta = _fetch(url, _response(429))
    assert meta['status'] == 429
    assert meta['retry_after'] is None
    assert meta['kind'] == kind_for_status(429)


@pytest.mark.parametrize('cache_control,expected', [
    ('public, max-age=600', 600),
    ('max-age=86400', 86400),
    ('max-age=0', None),
    ('no-cache', None),
    ('max-age=abc', None),
    ('max-age=-5', None),
    (None, None),
])
def test_max_age_captured_on_200(url, cache_control, expected):
    headers = {'Cache-Control': cache_control} if cache_control else {}
    result, meta = _fetch(url, _response(200, headers))
    assert result[0] == BODY.decode()
    assert meta['status'] == 200
    assert meta['max_age'] == expected
    assert meta['retry_after'] is None
    assert 'kind' not in meta


def test_max_age_captured_on_304(url):
    result, meta = _fetch(url, _response(304, {'Cache-Control': 'max-age=1800'}), etag='"a"')
    assert result == (None, '"a"', None)
    assert meta == {'status': 304, 'max_age': 1800, 'retry_after': None}


@pytest.mark.parametrize('error,kind', [
    (requests.exceptions.ConnectionError('HTTPSConnectionPool: NameResolutionError getaddrinfo failed'), 'dns'),
    (requests.exceptions.ConnectionError('[Errno -2] Name or service not known'), 'dns'),
    (requests.exceptions.SSLError('certificate verify failed'), 'tls'),
    (requests.exceptions.ReadTimeout('read timed out'), 'timeout'),
    (requests.exceptions.ConnectTimeout('connect timed out'), 'timeout'),
    (requests.exceptions.ConnectionError('Connection refused'), 'connection'),
    (SSRFError('Blocked private IP: 10.0.0.1'), 'blocked_address'),
])
def test_network_errors_map_to_kind(url, error, kind):
    result, meta = _fetch(url, error=error)
    assert result == (None, None, None)
    assert meta['kind'] == kind
    assert 'status' not in meta
    assert 'http' not in meta['detail'].lower()


def test_open_circuit_reports_connection(url):
    meta = {}
    with patch('rss_parser._get_rss_circuit_breaker') as get_breaker:
        get_breaker.return_value.check.side_effect = CircuitBreakerOpen('rss', 30)
        result = RSSParser().fetch_feed_conditional(url, meta=meta)
    assert result == (None, None, None)
    assert meta['kind'] == 'connection'


def test_each_fetch_starts_the_meta_it_is_given_empty(url):
    meta = {'status': 429, 'retry_after': '900', 'kind': 'rate_limited'}
    with patch('rss_parser.safe_get', return_value=_response(200)), \
            patch('rss_parser.read_response_capped', return_value=BODY):
        RSSParser().fetch_feed_conditional(url, meta=meta)
    assert meta == {'status': 200, 'max_age': None, 'retry_after': None}


def test_separate_meta_objects_never_share_values(url):
    first = _fetch(url, _response(429, {'Retry-After': '900'}))[1]
    second = _fetch(url + '2', _response(200))[1]
    assert first['retry_after'] == '900'
    assert second == {'status': 200, 'max_age': None, 'retry_after': None}


def test_open_circuit_meta_has_no_stale_http_facts(url):
    meta = {'status': 429, 'retry_after': '900'}
    with patch('rss_parser._get_rss_circuit_breaker') as get_breaker:
        get_breaker.return_value.check.side_effect = CircuitBreakerOpen('rss', 30)
        RSSParser().fetch_feed_conditional(url, meta=meta)
    assert 'retry_after' not in meta and 'status' not in meta


def test_gzip_retry_records_the_retry_response_headers(url):
    first = _response(200, {'Cache-Control': 'max-age=100'})
    retry = _response(200, {'Cache-Control': 'max-age=900'})
    meta = {}
    with patch('rss_parser.safe_get', side_effect=[first, retry]), \
            patch('rss_parser._read_capped_feed_body',
                  side_effect=[requests.exceptions.ContentDecodingError('bad gzip'), BODY]), \
            patch('rss_parser._mark_gzip_broken'):
        result = RSSParser().fetch_feed_conditional(url, meta=meta)
    assert result[0] == BODY.decode()
    assert meta['max_age'] == 900
