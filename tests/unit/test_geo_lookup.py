"""ipwho.is client and the background geo step."""
from unittest.mock import MagicMock, patch

import geo_lookup
from geo_lookup import run_geo_lookup

SLUG = 'example-podcast'
EPISODE = 'a1b2c3d4e5f6'


def _seed(db, ip):
    if not db.get_podcast_by_slug(SLUG):
        db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
    db.record_download(SLUG, EPISODE, ip, 'x')


def _statuses(db):
    rows = db.list_downloads(page=1, limit=5, sort_by='last_seen_at', sort_dir='desc')
    return {r['clientIp']: r['geoStatus'] for r in rows['downloads']}


def test_is_private():
    for ip in ('192.168.1.9', '10.0.0.1', '127.0.0.1', '169.254.1.1', '::1', 'fe80::1', 'garbage', ''):
        assert geo_lookup.is_private(ip), ip
    assert not geo_lookup.is_private('8.8.8.8')


def _reply(body):
    response = MagicMock(status_code=200)
    response.json.return_value = body
    return response


def test_lookup_ips_parses_response():
    replies = {
        '8.8.8.8': {'success': True, 'country': 'Exampleland', 'region': 'Region', 'city': 'Town'},
        '8.8.4.4': {'success': False, 'message': 'Invalid IP address'},
    }
    with patch('geo_lookup.requests.get',
               side_effect=lambda url, timeout: _reply(replies[url.split('/')[3].split('?')[0]])) as get:
        result = geo_lookup.lookup_ips(['8.8.8.8', '8.8.4.4'])
    assert result['8.8.8.8'] == {'status': 'ok', 'country': 'Exampleland',
                                 'region': 'Region', 'city': 'Town'}
    assert result['8.8.4.4']['status'] == 'failed'
    assert get.call_args.kwargs['timeout'] == 5
    assert get.call_args.args[0].startswith('https://ipwho.is/8.8.4.4')


def test_lookup_ips_omits_errors_and_handles_empty_input():
    with patch('geo_lookup.requests.get', side_effect=OSError('down')):
        assert geo_lookup.lookup_ips(['8.8.8.8']) == {}
    assert geo_lookup.lookup_ips([]) == {}


def test_lookup_ips_caps_per_tick():
    with patch('geo_lookup.requests.get', return_value=_reply({'success': True})) as get:
        geo_lookup.lookup_ips([f'8.8.{i // 250}.{i % 250}' for i in range(40)])
    assert get.call_count == geo_lookup.LOOKUPS_PER_TICK == 25


def test_private_ips_skip_the_network(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr('geo_lookup.lookup_ips', lambda ips: calls.append(ips) or {})
    temp_db.set_setting('download_geo_lookup_enabled', 'true', is_default=False)
    _seed(temp_db, '192.168.1.9')
    run_geo_lookup(temp_db)
    assert calls == []
    assert _statuses(temp_db) == {'192.168.1.9': 'private'}


def test_public_ips_are_looked_up_and_missing_stored_as_failed(temp_db, monkeypatch):
    temp_db.set_setting('download_geo_lookup_enabled', 'true', is_default=False)
    _seed(temp_db, '8.8.8.8')
    _seed(temp_db, '1.1.1.1')
    monkeypatch.setattr('geo_lookup.lookup_ips', lambda ips: {
        '8.8.8.8': {'status': 'ok', 'country': 'Exampleland', 'region': 'R', 'city': 'T'}})
    run_geo_lookup(temp_db)
    assert _statuses(temp_db) == {'8.8.8.8': 'ok', '1.1.1.1': 'failed'}


def test_disabled_setting_does_nothing(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr('geo_lookup.lookup_ips', lambda ips: calls.append(ips) or {})
    _seed(temp_db, '203.0.113.5')
    run_geo_lookup(temp_db)
    assert calls == []
    assert _statuses(temp_db) == {'203.0.113.5': None}
