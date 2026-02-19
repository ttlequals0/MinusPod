"""Tests for SSRF URL validation (src/utils/url.py)."""
import sys
import os
import socket
from unittest.mock import patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.url import validate_url, SSRFError


class TestValidSchemes:
    """Valid URL schemes should pass."""

    def test_http_allowed(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        ]):
            result = validate_url('http://example.com/feed.xml')
            assert result == 'http://example.com/feed.xml'

    def test_https_allowed(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        ]):
            result = validate_url('https://example.com/feed.xml')
            assert result == 'https://example.com/feed.xml'


class TestBlockedSchemes:
    """Non-http(s) schemes must be blocked."""

    def test_file_scheme_blocked(self):
        with pytest.raises(SSRFError, match="Blocked URL scheme"):
            validate_url('file:///etc/passwd')

    def test_ftp_scheme_blocked(self):
        with pytest.raises(SSRFError, match="Blocked URL scheme"):
            validate_url('ftp://internal.server/data')

    def test_gopher_scheme_blocked(self):
        with pytest.raises(SSRFError, match="Blocked URL scheme"):
            validate_url('gopher://evil.com/')

    def test_empty_scheme_blocked(self):
        with pytest.raises(SSRFError, match="Blocked URL scheme"):
            validate_url('://no-scheme.com')


class TestBlockedHosts:
    """Private, reserved, and loopback hosts must be blocked."""

    def test_localhost_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked loopback IP"):
                validate_url('http://localhost/admin')

    def test_127_0_0_1_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked loopback IP"):
                validate_url('http://127.0.0.1/')

    def test_10_x_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked private IP"):
                validate_url('http://10.0.0.1/')

    def test_172_16_x_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('172.16.0.1', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked private IP"):
                validate_url('http://172.16.0.1/')

    def test_192_168_x_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked private IP"):
                validate_url('http://192.168.1.1/')

    def test_ipv6_loopback_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 80, 0, 0))
        ]):
            with pytest.raises(SSRFError, match="Blocked loopback IP"):
                validate_url('http://[::1]/')

    def test_cloud_metadata_169_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked cloud metadata IP"):
                validate_url('http://169.254.169.254/latest/meta-data/')

    def test_azure_metadata_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('168.63.129.16', 80))
        ]):
            with pytest.raises(SSRFError, match="Blocked cloud metadata IP"):
                validate_url('http://168.63.129.16/')


class TestBlockedPorts:
    """Non-standard ports must be blocked."""

    def test_redis_port_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 6379))
        ]):
            with pytest.raises(SSRFError, match="Blocked port"):
                validate_url('http://example.com:6379/')

    def test_mysql_port_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 3306))
        ]):
            with pytest.raises(SSRFError, match="Blocked port"):
                validate_url('http://example.com:3306/')

    def test_ssh_port_blocked(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 22))
        ]):
            with pytest.raises(SSRFError, match="Blocked port"):
                validate_url('http://example.com:22/')


class TestAllowedPorts:
    """Standard web ports must be allowed."""

    def test_port_80_allowed(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        ]):
            validate_url('http://example.com:80/')

    def test_port_443_allowed(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        ]):
            validate_url('https://example.com:443/')

    def test_port_8080_allowed(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 8080))
        ]):
            validate_url('http://example.com:8080/')

    def test_port_8443_allowed(self):
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 8443))
        ]):
            validate_url('https://example.com:8443/')

    def test_default_http_port(self):
        """HTTP without explicit port should default to 80."""
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        ]):
            validate_url('http://example.com/feed')

    def test_default_https_port(self):
        """HTTPS without explicit port should default to 443."""
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        ]):
            validate_url('https://example.com/feed')


class TestEdgeCases:
    """Edge cases and malformed input."""

    def test_empty_url_raises(self):
        with pytest.raises(SSRFError, match="Empty URL"):
            validate_url('')

    def test_none_url_raises(self):
        with pytest.raises(SSRFError, match="Empty URL"):
            validate_url(None)

    def test_whitespace_only_raises(self):
        with pytest.raises(SSRFError, match="Empty URL"):
            validate_url('   ')

    def test_missing_hostname_raises(self):
        with pytest.raises(SSRFError, match="Missing hostname"):
            validate_url('http://')

    def test_unresolvable_hostname_raises(self):
        with patch('utils.url.socket.getaddrinfo', side_effect=socket.gaierror('not found')):
            with pytest.raises(SSRFError, match="Cannot resolve hostname"):
                validate_url('http://this-host-does-not-exist-xyz.example/')

    def test_url_is_stripped(self):
        """Leading/trailing whitespace should be stripped."""
        with patch('utils.url.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        ]):
            result = validate_url('  https://example.com/feed  ')
            assert result == 'https://example.com/feed'

    def test_ssrf_error_is_value_error(self):
        """SSRFError should be a subclass of ValueError."""
        assert issubclass(SSRFError, ValueError)


class TestSnippetSanitization:
    """Tests for search snippet XSS sanitization."""

    def test_mark_tags_preserved(self):
        import nh3
        snippet = 'found <mark>keyword</mark> in text'
        result = nh3.clean(snippet, tags={"mark"}, attributes={})
        assert result == 'found <mark>keyword</mark> in text'

    def test_script_tags_stripped(self):
        import nh3
        snippet = 'text <script>alert("xss")</script> more'
        result = nh3.clean(snippet, tags={"mark"}, attributes={})
        assert '<script>' not in result
        assert 'alert' not in result

    def test_img_onerror_stripped(self):
        import nh3
        snippet = 'text <img onerror="alert(1)" src=x> more'
        result = nh3.clean(snippet, tags={"mark"}, attributes={})
        assert '<img' not in result
        assert 'onerror' not in result

    def test_nested_html_stripped(self):
        import nh3
        snippet = '<mark>safe</mark> <b>bold</b> <a href="http://evil.com">link</a>'
        result = nh3.clean(snippet, tags={"mark"}, attributes={})
        assert '<mark>safe</mark>' in result
        assert '<b>' not in result
        assert '<a ' not in result

    def test_empty_snippet_passthrough(self):
        import nh3
        assert nh3.clean('', tags={"mark"}, attributes={}) == ''

    def test_plain_text_passthrough(self):
        import nh3
        snippet = 'just plain text with no html'
        result = nh3.clean(snippet, tags={"mark"}, attributes={})
        assert result == snippet
