"""MINUSPOD_PRIVATE_FEED_HOSTS: narrow allow-list for private feed source hosts."""
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('private_feed_hosts_test_')
from api import get_database
from utils import url as url_mod
from utils.safe_http import URLTrust, feed_trust, safe_get, validate_feed_source_url
from utils.url import SSRFError, parse_private_feed_hosts

ENV = 'MINUSPOD_PRIVATE_FEED_HOSTS'
ALL_ENV = 'MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS'


def _info(*ips):
    out = []
    for ip in ips:
        if ':' in ip:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, '', (ip, 80, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80)))
    return out


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv(ALL_ENV, raising=False)
    url_mod._cached_private_feed_hosts.cache_clear()
    yield
    url_mod._cached_private_feed_hosts.cache_clear()


def _check(monkeypatch, entries, url, ips):
    monkeypatch.setenv(ENV, entries)
    with patch('utils.url.socket.getaddrinfo', return_value=_info(*ips)):
        return validate_feed_source_url(url)


ALLOWED = [
    ('nas.lan', 'http://nas.lan/f.xml', ['192.168.1.50']),
    ('NAS.LAN.', 'http://nas.lan/f.xml', ['10.0.0.9']),
    ('nas.lan', 'http://NAS.lan./f.xml', ['10.0.0.9']),
    ('nas.lan', 'http://nas.lan/f.xml', ['fd00::5']),
    ('192.168.1.0/24', 'http://other.example/f.xml', ['192.168.1.7']),
    ('192.168.1.0/24', 'http://192.168.1.7/f.xml', ['192.168.1.7']),
    ('192.168.1.7', 'http://other.example/f.xml', ['192.168.1.7']),
    ('fd00::/8', 'http://other.example/f.xml', ['fd00::5', 'fd00::6']),
    ('127.0.0.0/8', 'http://localhost/f.xml', ['127.0.0.1']),
    ('169.254.0.0/16', 'http://other.example/f.xml', ['169.254.169.254']),
    ('[fd00::5]', 'http://other.example/f.xml', ['fd00::5']),
    ('nas.lan', 'http://nas.lan/f.xml', ['93.184.216.34']),
    ('nas.lan, 10.0.0.0/8', 'http://other.example/f.xml', ['10.1.2.3']),
    ('nas.lan', 'http://nas.lan/f.xml', ['100.64.0.5']),
]


@pytest.mark.parametrize('entries,url,ips', ALLOWED)
def test_allowed(monkeypatch, entries, url, ips):
    assert _check(monkeypatch, entries, url, ips) == url


REFUSED = [
    ('nas.lan', 'http://other.lan/f.xml', ['192.168.1.50']),
    ('192.168.1.0/24', 'http://other.example/f.xml', ['192.168.1.7', '10.0.0.1']),
    ('192.168.1.0/24', 'http://other.example/f.xml', ['192.168.1.7', '93.184.216.34', '10.0.0.1']),
    ('192.168.1.0/24', 'http://other.example/f.xml', ['192.168.2.7']),
    ('nas.lan', 'http://nas.lan/f.xml', ['127.0.0.1']),
    ('nas.lan', 'http://nas.lan/f.xml', ['::1']),
    ('nas.lan', 'http://nas.lan/f.xml', ['169.254.169.254']),
    ('nas.lan', 'http://nas.lan/f.xml', ['fe80::1']),
    ('nas.lan', 'http://nas.lan/f.xml', ['fd00:ec2::254']),
    ('nas.lan', 'http://nas.lan/f.xml', ['192.168.1.50', '127.0.0.1']),
    ('nas.lan', 'http://nas.lan/f.xml', ['::ffff:127.0.0.1']),
    ('10.0.0.0/8', 'http://nas.lan/f.xml', ['127.0.0.1']),
    ('nas.lan', 'http://192.168.1.50/f.xml', ['192.168.1.50']),
    ('fd00::/8', 'http://other.example/f.xml', ['fd00::5', 'fe80::1']),
]


@pytest.mark.parametrize('entries,url,ips', REFUSED)
def test_refused(monkeypatch, entries, url, ips):
    with pytest.raises(SSRFError):
        _check(monkeypatch, entries, url, ips)


def test_unlisted_private_refused_without_list(monkeypatch):
    with patch('utils.url.socket.getaddrinfo', return_value=_info('10.0.0.9')):
        with pytest.raises(SSRFError):
            validate_feed_source_url('http://nas.lan/f.xml')


def test_global_flag_still_allows_everything_private(monkeypatch):
    monkeypatch.setenv(ALL_ENV, 'true')
    monkeypatch.setenv(ENV, 'other.lan')
    assert feed_trust() is URLTrust.OPERATOR_CONFIGURED
    with patch('utils.url.socket.getaddrinfo', return_value=_info('127.0.0.1')):
        assert validate_feed_source_url('http://nas.lan/f.xml')


def test_feed_trust_tier(monkeypatch):
    assert feed_trust() is URLTrust.FEED_CONTENT
    monkeypatch.setenv(ENV, 'nas.lan')
    assert feed_trust() is URLTrust.FEED_SOURCE_LISTED
    monkeypatch.setenv(ENV, ' , ')
    assert feed_trust() is URLTrust.FEED_CONTENT


def test_invalid_entries_ignored_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='utils.url'):
        hosts = parse_private_feed_hosts('nas.lan, bad host, 10.0.0.0/33, *.lan, 192.168.0.0/16, ')
    assert hosts.hostnames == {'nas.lan'}
    assert [str(n) for n in hosts.networks] == ['192.168.0.0/16']
    warned = [r.getMessage() for r in caplog.records]
    assert len(warned) == 3
    for bad in ('bad host', '10.0.0.0/33', '*.lan'):
        assert any(bad in m for m in warned)


def test_env_parsed_once_with_single_warning(monkeypatch, caplog):
    monkeypatch.setenv(ENV, 'not a host')
    with caplog.at_level(logging.WARNING, logger='utils.url'):
        for _ in range(3):
            feed_trust()
    assert len(caplog.records) == 1


def test_enclosure_fetch_to_listed_host_stays_strict(monkeypatch):
    monkeypatch.setenv(ENV, 'nas.lan')
    with patch('utils.url.socket.getaddrinfo', return_value=_info('192.168.1.50')):
        with pytest.raises(SSRFError):
            safe_get('http://nas.lan/ep.mp3', URLTrust.FEED_CONTENT)
        with pytest.raises(SSRFError):
            validate_feed_source_url('http://other.lan/f.xml')
        assert validate_feed_source_url('http://nas.lan/f.xml')


@pytest.fixture
def any_port(monkeypatch):
    monkeypatch.setattr(url_mod, 'ALLOWED_URL_PORTS', set())


def _serve(handler):
    srv = HTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _resolver(mapping):
    real = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host in mapping:
            return _info(mapping[host])
        return real(host, port, *a, **kw)
    return fake


def test_connect_is_pinned_and_checked_on_connected_address(monkeypatch, any_port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *a):
            pass

    srv = _serve(Handler)
    try:
        port = srv.server_address[1]
        monkeypatch.setenv(ENV, '127.0.0.0/8')
        monkeypatch.setattr(socket, 'getaddrinfo', _resolver({'nas.lan': '127.0.0.1'}))
        resp = safe_get(f'http://nas.lan:{port}/f', feed_trust(), timeout=5)
        assert resp.status_code == 200
        # Listed by hostname only: the loopback address is refused at connect time.
        monkeypatch.setenv(ENV, 'nas.lan')
        with pytest.raises(SSRFError, match='loopback'):
            safe_get(f'http://nas.lan:{port}/f', feed_trust(), timeout=5)
    finally:
        srv.shutdown()


def test_rebind_after_validation_refused(monkeypatch):
    # First lookup (pre-flight) is the listed LAN address, the connect-time one is loopback.
    calls = {'n': 0}

    def flip(host, port, *a, **kw):
        calls['n'] += 1
        return _info('192.168.1.50' if calls['n'] == 1 else '127.0.0.1')

    monkeypatch.setenv(ENV, 'nas.lan')
    monkeypatch.setattr(socket, 'getaddrinfo', flip)
    with pytest.raises(SSRFError):
        safe_get('http://nas.lan/f', feed_trust(), timeout=2)


def test_redirect_to_unlisted_private_host_refused(monkeypatch, any_port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', f'http://other.lan:{self.server.server_address[1]}/f')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = _serve(Handler)
    try:
        port = srv.server_address[1]
        monkeypatch.setenv(ENV, 'nas.lan,127.0.0.0/8')
        monkeypatch.setattr(socket, 'getaddrinfo', _resolver(
            {'nas.lan': '127.0.0.1', 'other.lan': '192.168.1.99'}))
        with pytest.raises(SSRFError, match='192.168.1.99'):
            safe_get(f'http://nas.lan:{port}/f', feed_trust(), timeout=5)
    finally:
        srv.shutdown()


def test_redirect_to_listed_host_followed(monkeypatch, any_port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/f':
                self.send_response(302)
                self.send_header('Location', f'http://other.lan:{self.server.server_address[1]}/g')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *a):
            pass

    srv = _serve(Handler)
    try:
        port = srv.server_address[1]
        monkeypatch.setenv(ENV, '127.0.0.0/8')
        monkeypatch.setattr(socket, 'getaddrinfo', _resolver(
            {'nas.lan': '127.0.0.1', 'other.lan': '127.0.0.1'}))
        assert safe_get(f'http://nas.lan:{port}/f', feed_trust(), timeout=5).status_code == 200
    finally:
        srv.shutdown()


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def _add(client, url, slug):
    with patch('utils.url.socket.getaddrinfo', return_value=_info('192.168.1.10')), \
            patch('main_app.feeds.refresh_rss_feed'):
        return client.post('/api/v1/feeds', json={'sourceUrl': url, 'slug': slug},
                           headers=_csrf(client))


def test_add_feed_accepts_listed_and_rejects_unlisted(app_client, monkeypatch):
    monkeypatch.setenv(ENV, 'nas.lan')
    _authed(app_client)
    resp = _add(app_client, 'http://nas.lan/feed.xml', 'listed-lan')
    try:
        assert resp.status_code == 201, resp.get_json()
    finally:
        get_database().delete_podcast('listed-lan')
    resp = _add(app_client, 'http://unlisted.lan/feed.xml', 'unlisted-lan')
    assert resp.status_code == 400
    assert get_database().get_podcast_by_slug('unlisted-lan') is None
