"""MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS applies to feed URLs entering through the API, not only refresh."""
import socket
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_private_host_optin_test_')
from api import feeds as api_feeds
from api import get_database
from utils.safe_http import URLTrust, feed_trust, validate_feed_source_url
from utils.url import SSRFError

LAN_URL = 'http://192.168.1.10/feed.xml'
LAN_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.10', 80))]
ENV = 'MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS'


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def _add(client, slug):
    with patch('utils.url.socket.getaddrinfo', return_value=LAN_ADDRINFO), \
            patch('main_app.feeds.refresh_rss_feed'):
        return client.post('/api/v1/feeds', json={'sourceUrl': LAN_URL, 'slug': slug},
                           headers=_csrf(client))


@pytest.mark.parametrize('value', ['1', 'true', 'YES', ' on '])
def test_feed_trust_honours_opt_in(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert feed_trust() is URLTrust.OPERATOR_CONFIGURED


@pytest.mark.parametrize('value', [None, '', '0', 'false', 'maybe'])
def test_feed_trust_is_strict_by_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    assert feed_trust() is URLTrust.FEED_CONTENT


def test_validator_blocks_private_host_by_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    with patch('utils.url.socket.getaddrinfo', return_value=LAN_ADDRINFO):
        with pytest.raises(SSRFError, match='private'):
            validate_feed_source_url(LAN_URL)


def test_validator_allows_private_host_with_opt_in(monkeypatch):
    monkeypatch.setenv(ENV, 'true')
    with patch('utils.url.socket.getaddrinfo', return_value=LAN_ADDRINFO):
        assert validate_feed_source_url(LAN_URL) == LAN_URL


def test_validator_still_blocks_metadata_with_opt_in(monkeypatch):
    monkeypatch.setenv(ENV, 'true')
    with pytest.raises(SSRFError, match='metadata'):
        validate_feed_source_url('http://169.254.169.254/latest')


def test_add_feed_rejects_private_host_by_default(app_client, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _authed(app_client)
    resp = _add(app_client, 'lan-default')
    assert resp.status_code == 400
    assert get_database().get_podcast_by_slug('lan-default') is None


def test_add_feed_accepts_private_host_with_opt_in(app_client, monkeypatch):
    monkeypatch.setenv(ENV, 'true')
    _authed(app_client)
    resp = _add(app_client, 'lan-optin')
    try:
        assert resp.status_code == 201, resp.get_json()
    finally:
        get_database().delete_podcast('lan-optin')


def test_update_feed_source_url_accepts_private_host_with_opt_in(monkeypatch):
    monkeypatch.setenv(ENV, 'true')
    with patch('utils.url.socket.getaddrinfo', return_value=LAN_ADDRINFO), \
            patch('api.feeds._fetch_feed_content', return_value=(None, b'')):
        _url, error = api_feeds._validate_source_url(LAN_URL)
    assert 'Invalid feed URL' not in (error or '')


@pytest.mark.parametrize('opt_in,expected', [
    ('true', URLTrust.OPERATOR_CONFIGURED), (None, URLTrust.FEED_CONTENT)])
def test_audio_availability_check_uses_feed_trust(monkeypatch, opt_in, expected):
    from transcriber import Transcriber
    if opt_in is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, opt_in)
    with patch('utils.safe_http.safe_head') as head:
        head.return_value.status_code = 200
        head.return_value.headers = {}
        Transcriber.check_audio_availability(object(), 'http://192.168.1.10/a.mp3')
    assert head.call_args.kwargs['trust'] is expected
