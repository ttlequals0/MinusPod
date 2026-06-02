"""Regression tests for the no-password fail-closed gate (api-rest-4) and the
IPv4-mapped IPv6 lockout normalization (midsize-backend-1)."""
from api import _blocked_before_bootstrap
from utils.validation import is_public_ip_for_lockout


def test_blocked_before_bootstrap_denies_sensitive_routes():
    # Secret exfil + destructive + secret-using routes are closed before setup.
    assert _blocked_before_bootstrap('/api/v1/system/backup', 'GET') is True
    assert _blocked_before_bootstrap('/api/v1/system/cleanup', 'POST') is True
    assert _blocked_before_bootstrap('/api/v1/providers/anthropic', 'PUT') is True
    assert _blocked_before_bootstrap('/api/v1/feeds/some-slug', 'DELETE') is True
    assert _blocked_before_bootstrap('/api/v1/feeds/some-slug', 'PATCH') is True


def test_blocked_before_bootstrap_allows_read_and_setup_paths():
    # Read-only browsing + first-run feed add stay open so setup can proceed.
    assert _blocked_before_bootstrap('/api/v1/feeds', 'GET') is False
    assert _blocked_before_bootstrap('/api/v1/feeds', 'POST') is False
    assert _blocked_before_bootstrap('/api/v1/settings/ad-detection', 'PUT') is False
    assert _blocked_before_bootstrap('/api/v1/health', 'GET') is False


def test_ipv4_mapped_ipv6_public_counts_as_public():
    assert is_public_ip_for_lockout('::ffff:8.8.8.8') is True
    assert is_public_ip_for_lockout('8.8.8.8') is True


def test_ipv4_mapped_ipv6_private_does_not_count_as_public():
    assert is_public_ip_for_lockout('::ffff:192.168.1.1') is False
    assert is_public_ip_for_lockout('192.168.1.1') is False
