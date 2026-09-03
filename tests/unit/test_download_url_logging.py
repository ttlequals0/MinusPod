"""Tests for how much of a download URL reaches the logs.

A failed download used to log the host and nothing else, so repeated failures
left no record of what was being fetched. The path and the redirect chain are
now logged; the query string stays opt-in because a podcast enclosure
regularly carries a signed CDN token or a per-listener tracking id there, and
logs outlive both.
"""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('download_log_test_')
from utils.http import redirect_chain_for_log, safe_url_for_log

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
