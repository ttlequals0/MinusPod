"""Tests for utils.safe_http trust-tier validation and redirect guards."""
from unittest.mock import MagicMock, patch

import pytest

from utils.safe_http import (
    URLTrust,
    RedirectContext,
    REDIRECT_LIMITS,
    ResponseTooLargeError,
    read_response_capped,
    _reject_https_downgrade,
    _validate_for_tier,
    safe_get,
)
from utils.url import SSRFError


def test_redirect_limits_covers_all_contexts():
    for ctx in RedirectContext:
        assert ctx in REDIRECT_LIMITS


def test_read_response_capped_accepts_under_cap():
    chunks = [b'a' * 100, b'b' * 100]
    response = MagicMock()
    response.iter_content = lambda chunk_size: iter(chunks)
    result = read_response_capped(response, 1000)
    assert len(result) == 200


def test_read_response_capped_rejects_over_cap():
    chunks = [b'a' * 60, b'b' * 60]
    response = MagicMock()
    response.iter_content = lambda chunk_size: iter(chunks)
    with pytest.raises(ResponseTooLargeError):
        read_response_capped(response, 100)


def test_reject_https_downgrade_blocks():
    with pytest.raises(SSRFError):
        _reject_https_downgrade('https://a.example.com', 'http://b.example.com')


def test_reject_https_downgrade_allows_same_scheme():
    _reject_https_downgrade('https://a.example.com', 'https://b.example.com')
    _reject_https_downgrade('http://a.example.com', 'http://b.example.com')


def test_validate_for_tier_feed_rejects_private():
    with pytest.raises(SSRFError):
        _validate_for_tier('http://127.0.0.1/feed.xml', URLTrust.FEED_CONTENT)


def test_validate_for_tier_operator_allows_private():
    # validate_base_url on a private IP should pass
    _validate_for_tier('http://192.168.1.10/v1', URLTrust.OPERATOR_CONFIGURED)


def test_validate_for_tier_operator_rejects_metadata():
    with pytest.raises(SSRFError):
        _validate_for_tier('http://169.254.169.254/', URLTrust.OPERATOR_CONFIGURED)


def test_safe_get_rejects_initial_private_under_feed_trust():
    with pytest.raises(SSRFError):
        safe_get('http://192.168.1.1/art.jpg', URLTrust.FEED_CONTENT)


def test_safe_get_invokes_session_get():
    """End-to-end: a whitelisted URL actually reaches the session."""
    with patch('utils.safe_http._RevalidatingSession.get') as mock_get:
        mock_resp = MagicMock()
        mock_get.return_value = mock_resp
        result = safe_get('https://api.openrouter.ai/v1/models', URLTrust.OPERATOR_CONFIGURED)
        assert result is mock_resp
        mock_get.assert_called_once()
