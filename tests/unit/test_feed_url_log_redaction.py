"""Feed URLs carry the subscriber token in the path: no log record may hold it, and one
upstream failure is one actionable WARNING with the cause."""
import logging
from unittest.mock import patch

import pytest
import requests

from tests.app_bootstrap import bootstrap

bootstrap('feed_url_log_redaction_test_')

import rss_parser as rss_module
from main_app import feeds
from rss_parser import RSSParser
from utils.url import SSRFError

TOKEN = 'TOKENabc123' * 8
URL = f'https://feeds.example.com/content/{TOKEN}?auth=QSECRET'
HTTP_ERROR = requests.HTTPError(f'502 Server Error: Bad Gateway for url: {URL}')


def _text(caplog):
    return '\n'.join(r.getMessage() for r in caplog.records)


@pytest.fixture(autouse=True)
def _fresh_breaker():
    rss_module._rss_circuit_breakers.clear()
    yield


def _refresh(slug='example-podcast', side_effect=None):
    parser = RSSParser()
    podcast = {'title': 'Example', 'etag': None, 'last_modified_header': None}
    with patch('rss_parser.safe_get', side_effect=side_effect or HTTP_ERROR), \
         patch.object(feeds, 'rss_parser', parser), \
         patch.object(feeds, 'db') as db, \
         patch.object(feeds, 'status_service'), \
         patch.object(feeds, '_record_refresh_failure'), \
         patch.object(feeds, '_parse_backoff_remaining', return_value=0.0):
        db.get_podcast_row.return_value = podcast
        return feeds.refresh_rss_feed(slug, URL, force=True)


def test_refresh_logs_never_hold_path_or_query(caplog):
    caplog.set_level(logging.DEBUG)
    _refresh()
    text = _text(caplog)
    assert TOKEN not in text
    assert 'QSECRET' not in text
    assert 'feeds.example.com' in text


def test_refresh_start_line_is_scheme_and_host_only(caplog):
    caplog.set_level(logging.INFO)
    _refresh()
    start = [r.getMessage() for r in caplog.records if 'Starting RSS refresh' in r.getMessage()]
    assert start == ['[example-podcast] Starting RSS refresh from: https://feeds.example.com']


def test_refresh_failure_is_one_warning_with_cause_and_one_info(caplog):
    caplog.set_level(logging.DEBUG)
    outcome = _refresh()
    assert outcome.status == 'fetch_failed'
    loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(loud) == 1
    assert loud[0].levelno == logging.WARNING
    msg = loud[0].getMessage()
    assert '502 Server Error: Bad Gateway' in msg
    assert 'https://feeds.example.com' in msg
    assert any(r.levelno == logging.INFO and r.getMessage() ==
               '[example-podcast] Feed refresh failed; will retry next refresh'
               for r in caplog.records)


METHODS = ['fetch_feed', 'fetch_feed_conditional']


@pytest.mark.parametrize('method', METHODS)
def test_failure_is_one_warning_hiding_path_and_query(caplog, method):
    caplog.set_level(logging.DEBUG)
    with patch('rss_parser.safe_get', side_effect=HTTP_ERROR):
        getattr(RSSParser(), method)(URL)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    text = _text(caplog)
    assert TOKEN not in text and 'QSECRET' not in text
    assert '502 Server Error: Bad Gateway' in text
    assert 'https://feeds.example.com' in text


@pytest.mark.parametrize('method', METHODS)
def test_connection_error_path_only_url_is_hidden(caplog, method):
    caplog.set_level(logging.DEBUG)
    err = requests.ConnectionError(
        f"HTTPSConnectionPool(host='feeds.example.com', port=443): Max retries exceeded "
        f"with url: /content/{TOKEN}?auth=QSECRET (Caused by timeout)")
    with patch('rss_parser.safe_get', side_effect=err):
        getattr(RSSParser(), method)(URL)
    text = _text(caplog)
    assert TOKEN not in text and 'QSECRET' not in text
    assert 'Max retries exceeded' in text


@pytest.mark.parametrize('method', METHODS)
def test_gzip_retry_failure_is_two_warnings_without_secrets(caplog, method):
    caplog.set_level(logging.DEBUG)
    gzip_err = requests.exceptions.ContentDecodingError(f'bad gzip for url: {URL}')
    with patch('rss_parser.safe_get', side_effect=[gzip_err, HTTP_ERROR]):
        getattr(RSSParser(), method)(URL)
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 2
    text = _text(caplog)
    assert TOKEN not in text and 'QSECRET' not in text
    assert '502 Server Error: Bad Gateway' in text


@pytest.mark.parametrize('method', METHODS)
def test_ssrf_redirect_target_hides_path(caplog, method):
    caplog.set_level(logging.DEBUG)
    err = SSRFError(f'HTTPS -> HTTP redirect blocked: http://feeds.example.com/content/{TOKEN}')
    with patch('rss_parser.safe_get', side_effect=err):
        getattr(RSSParser(), method)(URL)
    assert TOKEN not in _text(caplog)


def test_https_downgrade_error_keeps_host_only():
    from utils.safe_http import _reject_https_downgrade
    with pytest.raises(SSRFError) as exc:
        _reject_https_downgrade('https://a.example.com/x', f'http://feeds.example.com/p/{TOKEN}?k=QSECRET')
    assert 'feeds.example.com' in str(exc.value)
    assert TOKEN not in str(exc.value) and 'QSECRET' not in str(exc.value)
