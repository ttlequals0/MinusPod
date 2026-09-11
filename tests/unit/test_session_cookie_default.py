"""Tests for _default_session_cookie_secure helper (issue #460).

The helper is extracted from the SESSION_COOKIE_SECURE config assembly so it is
unit-testable independently of the Flask app startup sequence.
"""
from utils.session_defaults import _default_session_cookie_secure as fn


class TestDefaultSessionCookieSecure:
    def test_unset_base_url_https_returns_true(self, monkeypatch):
        """No explicit env var + BASE_URL=https -> Secure=True (HTTPS deploy)."""
        monkeypatch.delenv('SESSION_COOKIE_SECURE', raising=False)
        monkeypatch.setenv('BASE_URL', 'https://example.com')
        assert fn() is True

    def test_unset_base_url_http_returns_false(self, monkeypatch):
        """No explicit env var + BASE_URL=http -> Secure=False (plain HTTP deploy)."""
        monkeypatch.delenv('SESSION_COOKIE_SECURE', raising=False)
        monkeypatch.setenv('BASE_URL', 'http://localhost:8000')
        assert fn() is False

    def test_unset_base_url_empty_returns_true(self, monkeypatch):
        """No explicit env var + no BASE_URL -> Secure=True (secure by default)."""
        monkeypatch.delenv('SESSION_COOKIE_SECURE', raising=False)
        monkeypatch.delenv('BASE_URL', raising=False)
        assert fn() is True

    def test_explicit_true_overrides_base_url(self, monkeypatch):
        """Explicit SESSION_COOKIE_SECURE=true is honored even over http BASE_URL."""
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'true')
        monkeypatch.setenv('BASE_URL', 'http://localhost:8000')
        assert fn() is True

    def test_explicit_false_overrides_base_url(self, monkeypatch):
        """Explicit SESSION_COOKIE_SECURE=false is honored even over https BASE_URL."""
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'false')
        monkeypatch.setenv('BASE_URL', 'https://example.com')
        assert fn() is False

    def test_explicit_true_uppercase(self, monkeypatch):
        """Case-insensitive: TRUE -> True."""
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'TRUE')
        monkeypatch.delenv('BASE_URL', raising=False)
        assert fn() is True

    def test_https_mixed_case_base_url(self, monkeypatch):
        """HTTPS prefix is case-insensitive."""
        monkeypatch.delenv('SESSION_COOKIE_SECURE', raising=False)
        monkeypatch.setenv('BASE_URL', 'HTTPS://example.com')
        assert fn() is True

    def test_explicit_value_whitespace_stripped(self, monkeypatch):
        """Explicit value tolerates surrounding whitespace."""
        monkeypatch.setenv('SESSION_COOKIE_SECURE', '  true  ')
        monkeypatch.delenv('BASE_URL', raising=False)
        assert fn() is True

    def test_auto_uses_plaintext_base_url(self, monkeypatch):
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'auto')
        monkeypatch.setenv('BASE_URL', 'http://localhost:8000')
        assert fn() is False

    def test_auto_uses_https_base_url(self, monkeypatch):
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'AUTO')
        monkeypatch.setenv('BASE_URL', 'https://example.com')
        assert fn() is True
