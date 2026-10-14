"""ip-api.com batch client and the background geo step."""
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


def test_lookup_batch_parses_response():
    response = MagicMock(status_code=200)
    response.json.return_value = [
        {'status': 'success', 'query': '8.8.8.8', 'country': 'Exampleland',
         'regionName': 'Region', 'city': 'Town'},
        {'status': 'fail', 'query': '8.8.4.4'},
    ]
    with patch('geo_lookup.requests.post', return_value=response) as post:
        result = geo_lookup.lookup_batch(['8.8.8.8', '8.8.4.4'])
    assert result['8.8.8.8'] == {'status': 'ok', 'country': 'Exampleland',
                                 'region': 'Region', 'city': 'Town'}
    assert result['8.8.4.4']['status'] == 'failed'
    assert post.call_args.kwargs['timeout'] == 10
    assert post.call_args.args[0].startswith('http://ip-api.com/batch')


def test_lookup_batch_swallows_errors_and_empty_input():
    with patch('geo_lookup.requests.post', side_effect=OSError('down')):
        assert geo_lookup.lookup_batch(['8.8.8.8']) == {}
    assert geo_lookup.lookup_batch([]) == {}


def test_lookup_batch_sends_at_most_100(monkeypatch):
    response = MagicMock(status_code=200)
    response.json.return_value = []
    with patch('geo_lookup.requests.post', return_value=response) as post:
        geo_lookup.lookup_batch([f'8.8.{i // 250}.{i % 250}' for i in range(150)])
    assert len(post.call_args.kwargs['json']) == 100


def test_private_ips_skip_the_network(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr('geo_lookup.lookup_batch', lambda ips: calls.append(ips) or {})
    temp_db.set_setting('download_geo_lookup_enabled', 'true', is_default=False)
    _seed(temp_db, '192.168.1.9')
    run_geo_lookup(temp_db)
    assert calls == []
    assert _statuses(temp_db) == {'192.168.1.9': 'private'}


def test_public_ips_are_looked_up_and_missing_stored_as_failed(temp_db, monkeypatch):
    temp_db.set_setting('download_geo_lookup_enabled', 'true', is_default=False)
    _seed(temp_db, '8.8.8.8')
    _seed(temp_db, '1.1.1.1')
    monkeypatch.setattr('geo_lookup.lookup_batch', lambda ips: {
        '8.8.8.8': {'status': 'ok', 'country': 'Exampleland', 'region': 'R', 'city': 'T'}})
    run_geo_lookup(temp_db)
    assert _statuses(temp_db) == {'8.8.8.8': 'ok', '1.1.1.1': 'failed'}


def test_disabled_setting_does_nothing(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr('geo_lookup.lookup_batch', lambda ips: calls.append(ips) or {})
    _seed(temp_db, '203.0.113.5')
    run_geo_lookup(temp_db)
    assert calls == []
    assert _statuses(temp_db) == {'203.0.113.5': None}
