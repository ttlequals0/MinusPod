from tests.app_bootstrap import bootstrap  # noqa: F401

import pytest
from flask import Flask, jsonify, request

from utils.trusted_proxy import PeerGatedProxyFix, direct_peer, parse_peer, parse_trusted_networks


def _client(raw=''):
    app = Flask(__name__)

    @app.route('/ip')
    def ip():
        return jsonify(addr=request.remote_addr, direct=direct_peer(request.environ))

    app.wsgi_app = PeerGatedProxyFix(app.wsgi_app, 1, parse_trusted_networks(raw))
    return app.test_client()


def _addr(client, peer, xff='198.51.100.7'):
    resp = client.get('/ip', headers={'X-Forwarded-For': xff}, environ_base={'REMOTE_ADDR': peer})
    return resp.get_json()['addr']


@pytest.mark.parametrize('peer', ['203.0.113.10', '2001:db8::1', 'not-an-ip'])
def test_untrusted_peer_forwarded_for_is_ignored(peer):
    assert _addr(_client(), peer) == peer


@pytest.mark.parametrize('peer', ['127.0.0.1', '::1', '10.1.2.3', '172.18.0.1', '192.168.1.5', '100.64.0.2', 'fd00::1'])
def test_default_trusted_peer_forwarded_for_is_honored(peer):
    assert _addr(_client(), peer) == '198.51.100.7'


def test_explicit_list_replaces_the_default():
    client = _client('203.0.113.0/24, 192.0.2.9')
    assert _addr(client, '203.0.113.50') == '198.51.100.7'
    assert _addr(client, '192.0.2.9') == '198.51.100.7'
    assert _addr(client, '127.0.0.1') == '127.0.0.1'


def test_invalid_entries_are_skipped_with_a_warning(caplog):
    with caplog.at_level('WARNING'):
        nets = parse_trusted_networks('bogus,10.0.0.0/8')
    assert [str(n) for n in nets] == ['10.0.0.0/8']
    assert 'invalid' in caplog.text


def test_mapped_ipv4_peer_matches_ipv4_network():
    assert _addr(_client('10.0.0.0/8'), '::ffff:10.1.1.1') == '198.51.100.7'


@pytest.mark.parametrize('peer', ['203.0.113.10', '127.0.0.1'])
def test_direct_peer_is_the_socket_address_whether_or_not_trusted(peer):
    resp = _client().get('/ip', headers={'X-Forwarded-For': '198.51.100.7'}, environ_base={'REMOTE_ADDR': peer})
    assert resp.get_json()['direct'] == peer


def test_direct_peer_falls_back_to_remote_addr_without_the_middleware():
    assert direct_peer({'REMOTE_ADDR': '192.0.2.1'}) == '192.0.2.1'


def test_parse_peer_unwraps_mapped_ipv4_and_rejects_junk():
    assert str(parse_peer('::ffff:127.0.0.1')) == '127.0.0.1'
    assert parse_peer('not-an-ip') is None
    assert parse_peer(None) is None
