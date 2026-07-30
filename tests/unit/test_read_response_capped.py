"""Body-completeness enforcement in read_response_capped.

The helper capped the upper bound only: it raised above max_bytes and otherwise
returned whatever iter_content yielded. A connection truncated mid-body ends the
iterator without raising, so a short read came back as a successful fetch and
callers parsed a fragment as if it were the whole document.
"""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('read_capped_test_')

from utils.safe_http import (  # noqa: E402
    IncompleteResponseError, ResponseTooLargeError, read_response_capped,
)


class FakeResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    def iter_content(self, chunk_size=65536):
        return iter(self._chunks)


def test_returns_full_body_when_length_matches():
    r = FakeResponse([b'abc', b'def'], {'Content-Length': '6'})
    assert read_response_capped(r, 1000) == b'abcdef'


def test_raises_when_body_shorter_than_declared_length():
    r = FakeResponse([b'abc'], {'Content-Length': '6'})
    with pytest.raises(IncompleteResponseError):
        read_response_capped(r, 1000)


def test_empty_body_with_declared_length_raises():
    r = FakeResponse([], {'Content-Length': '6'})
    with pytest.raises(IncompleteResponseError):
        read_response_capped(r, 1000)


def test_no_content_length_returns_what_arrived():
    """Chunked transfer declares no length, so a short read is indistinguishable
    from a complete one and must not be rejected."""
    r = FakeResponse([b'abc'])
    assert read_response_capped(r, 1000) == b'abc'


def test_unparseable_content_length_is_ignored():
    r = FakeResponse([b'abc'], {'Content-Length': 'banana'})
    assert read_response_capped(r, 1000) == b'abc'


def test_zero_declared_length_accepts_empty_body():
    r = FakeResponse([], {'Content-Length': '0'})
    assert read_response_capped(r, 1000) == b''


def test_longer_than_declared_length_is_not_an_error():
    """Only a short read is evidence of truncation; a stale or conservative
    length header is not."""
    r = FakeResponse([b'abcdefgh'], {'Content-Length': '6'})
    assert read_response_capped(r, 1000) == b'abcdefgh'


def test_still_raises_above_max_bytes():
    r = FakeResponse([b'a' * 10], {'Content-Length': '10'})
    with pytest.raises(ResponseTooLargeError):
        read_response_capped(r, 5)


def test_size_cap_takes_precedence_over_completeness():
    """A response that is both over the cap and short must report the cap, since
    that is the decision the caller acts on."""
    r = FakeResponse([b'a' * 10], {'Content-Length': '99'})
    with pytest.raises(ResponseTooLargeError):
        read_response_capped(r, 5)


def test_empty_chunks_are_skipped_and_not_counted():
    r = FakeResponse([b'abc', b'', b'def'], {'Content-Length': '6'})
    assert read_response_capped(r, 1000) == b'abcdef'


class TestContentEncoding:
    """iter_content decodes gzip, so Content-Length counts encoded bytes while
    the buffer holds decoded ones. On a small payload the encoded size is the
    larger, which would read as a short body."""

    def test_gzipped_short_decoded_body_is_not_truncation(self):
        r = FakeResponse([b'{"a":1}'],
                         {'Content-Length': '27', 'Content-Encoding': 'gzip'})
        assert read_response_capped(r, 1000) == b'{"a":1}'

    def test_identity_encoding_still_checks_length(self):
        r = FakeResponse([b'abc'],
                         {'Content-Length': '6', 'Content-Encoding': 'identity'})
        with pytest.raises(IncompleteResponseError):
            read_response_capped(r, 1000)

    def test_br_encoding_skips_the_check_too(self):
        r = FakeResponse([b'abc'],
                         {'Content-Length': '99', 'Content-Encoding': 'br'})
        assert read_response_capped(r, 1000) == b'abc'

    def test_size_cap_still_applies_to_an_encoded_body(self):
        r = FakeResponse([b'a' * 10], {'Content-Encoding': 'gzip'})
        with pytest.raises(ResponseTooLargeError):
            read_response_capped(r, 5)
