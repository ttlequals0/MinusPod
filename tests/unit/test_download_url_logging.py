"""Tests for how much of a download URL reaches the logs.

The host, path and redirect chain are logged; the query string stays opt-in because an enclosure URL
often carries a signed CDN token or per-listener tracking id, and logs outlive both."""
import logging

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('download_log_test_')
import transcriber
from run_log import _MESSAGE_FORMATTER
from utils.http import redirect_chain_for_log, safe_url_for_log
from utils.log_format import QueryStringRedactingFilter, make_formatter

SIGNED = 'https://cdn.example.net/f/234.mp3?token=SECRET123&exp=1788400000'


class TestSafeUrlForLog:
    def test_default_keeps_host_only(self):
        assert safe_url_for_log(SIGNED) == 'https://cdn.example.net'

    def test_keep_path_adds_the_path_but_not_the_query(self):
        assert safe_url_for_log(SIGNED, keep_path=True) == \
            'https://cdn.example.net/f/234.mp3'

    def test_keep_query_adds_both(self):
        assert safe_url_for_log(SIGNED, keep_query=True) == SIGNED

    def test_keep_query_implies_the_path(self):
        """Otherwise the query would attach to a bare host and read as a
        different URL than the one actually fetched."""
        assert '/f/234.mp3?' in safe_url_for_log(SIGNED, keep_query=True)

    def test_fragment_is_always_dropped(self):
        assert safe_url_for_log('https://h/a.mp3?q=1#frag', keep_query=True) == \
            'https://h/a.mp3?q=1'

    @pytest.mark.parametrize("url,expected", [
        ('http://user:pass@127.0.0.1:9101/v1?key=x', 'http://127.0.0.1:9101'),
        ('http://127.0.0.1:9102/v1', 'http://127.0.0.1:9102'),
        ('http://[::1]:8000/v1', 'http://[::1]:8000'),
        ('http://example.com:notaport/v1', 'http://example.com'),
    ])
    def test_port_is_kept_and_credentials_dropped(self, url, expected):
        assert safe_url_for_log(url) == expected

    @pytest.mark.parametrize("bad", [None, '', 'not a url', 12345])
    def test_unparseable_input_reduces_to_a_sentinel(self, bad):
        assert safe_url_for_log(bad, keep_query=True) == '<url>'


class _Hop:
    def __init__(self, status_code, location, url=None):
        self.status_code = status_code
        self.headers = {'Location': location} if location else {}
        if url:
            self.url = url


class _Response:
    def __init__(self, history, url):
        self.history = history
        self.url = url


class TestRedirectChainForLog:
    def test_no_redirect_produces_no_lines(self):
        """So a caller can splice the result in without a special case."""
        assert redirect_chain_for_log(_Response([], 'https://h/a.mp3')) == []

    def test_each_hop_is_numbered_with_its_status_and_path(self):
        response = _Response(
            [_Hop(302, 'https://cdn.example.net/f/234.mp3')],
            'https://cdn.example.net/f/234.mp3')
        assert redirect_chain_for_log(response) == [
            '  redirect 1 (302): https://cdn.example.net/f/234.mp3',
            '  final: https://cdn.example.net/f/234.mp3',
        ]

    def test_the_query_stays_out_unless_asked_for(self):
        response = _Response([_Hop(302, SIGNED)], SIGNED)
        assert 'SECRET123' not in ' '.join(redirect_chain_for_log(response))
        assert 'SECRET123' in ' '.join(
            redirect_chain_for_log(response, keep_query=True))

    def test_a_hop_with_no_location_header_is_marked_unknown(self):
        response = _Response([_Hop(302, None)], None)
        assert redirect_chain_for_log(response)[0].endswith('<unknown>')

    def test_a_relative_location_logs_the_resolved_target(self):
        response = _Response(
            [_Hop(302, '/media/ep.mp3?token=x'),
             _Hop(302, '//cdn.example.net/a.mp3', url='https://h/media/ep.mp3?token=x')],
            'https://cdn.example.net/a.mp3')
        lines = redirect_chain_for_log(response)
        assert lines[0] == '  redirect 1 (302): https://h/media/ep.mp3'
        assert lines[1] == '  redirect 2 (302): https://cdn.example.net/a.mp3'

    def test_a_response_without_history_is_tolerated(self):
        """Test doubles and non-requests responses must not raise here."""
        assert redirect_chain_for_log(object()) == []


def test_query_logging_is_off_by_default():
    from config import resolve_env_backed_default
    assert resolve_env_backed_default('log_download_query') == 'false'


_KEY = 'ab' * 32
_MARKED_URL = f'https://u:PASS@cdn.example.net/{_KEY}/234.mp3?token=SECRET123'


class _Head:
    status_code = 200
    history = [_Hop(302, SIGNED)]
    url = SIGNED


@pytest.fixture
def captured(monkeypatch):
    """Records from the transcriber through each app sink: text, JSON and run log formatters."""

    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Keep()
    handler.addFilter(QueryStringRedactingFilter())
    log = logging.getLogger('transcriber')
    monkeypatch.setattr(log, 'level', logging.INFO)
    log.addHandler(handler)
    yield lambda: [f.format(r) for r in records
                   for f in (make_formatter('text'), make_formatter('json'), _MESSAGE_FORMATTER)]
    log.removeHandler(handler)


@pytest.mark.parametrize("opt_in", [True, False])
def test_availability_check_lines_follow_the_query_opt_in(monkeypatch, captured, opt_in):
    monkeypatch.setattr(transcriber, 'log_download_query_enabled', lambda: opt_in)
    monkeypatch.setattr('utils.safe_http.safe_head', lambda *a, **k: _Head())
    assert transcriber.Transcriber().check_audio_availability(_MARKED_URL) == (True, None)
    out = '\n'.join(captured())
    assert ('token=SECRET123' in out) is opt_in
    assert 'PASS' not in out and _KEY not in out


@pytest.mark.parametrize("opt_in", [True, False])
def test_download_lines_follow_the_query_opt_in(monkeypatch, captured, opt_in):
    monkeypatch.setattr(transcriber, 'log_download_query_enabled', lambda: opt_in)

    def refuse(*a, **k):
        raise transcriber.SSRFError('blocked')
    monkeypatch.setattr(transcriber, 'safe_get', refuse)
    assert transcriber.Transcriber().download_audio(_MARKED_URL) is None
    out = '\n'.join(captured())
    assert ('token=SECRET123' in out) is opt_in
    assert 'PASS' not in out and _KEY not in out


def test_an_unmarked_line_with_a_query_stays_redacted_under_the_opt_in(monkeypatch, captured):
    monkeypatch.setattr(transcriber, 'log_download_query_enabled', lambda: True)
    logging.getLogger('transcriber').info(f'other {SIGNED}')
    assert not any('SECRET123' in line for line in captured())
