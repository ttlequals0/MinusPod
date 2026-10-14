"""Download history: dedupe, listing, pruning and geo cache bookkeeping."""
import json
from unittest.mock import patch

import pytest

from database import downloads
from database.downloads import capture_request

SLUG = 'example-podcast'
EPISODE = 'a1b2c3d4e5f6'
IP = '203.0.113.5'


@pytest.fixture
def db(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
    temp_db.upsert_episode(SLUG, EPISODE, original_url='https://example.com/e.mp3',
                           title='Episode One')
    return temp_db


def _rows(db, **kwargs):
    params = dict(page=1, limit=10, sort_by='last_seen_at', sort_dir='desc')
    params.update(kwargs)
    return db.list_downloads(**params)


def test_dedupes_within_window(db):
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:00Z')
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:05:00Z')
    result = _rows(db)
    assert result['total'] == 1
    row = result['downloads'][0]
    assert row['requestCount'] == 2
    assert row['firstSeenAt'] == '2026-10-14T10:00:00Z'
    assert row['lastSeenAt'] == '2026-10-14T10:05:00Z'
    assert row['episodeTitle'] == 'Episode One'
    assert row['podcastSlug'] == SLUG and row['podcastTitle'] == 'Example Podcast'
    assert row['subscriberLabel'] is None


def test_burst_repeat_takes_no_write_lock(db):
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:00Z')
    with patch.object(db, 'transaction') as transaction:
        db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:10Z')
    transaction.assert_not_called()
    assert _rows(db)['downloads'][0]['requestCount'] == 1


def test_repeat_after_two_minutes_bumps(db):
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:00Z')
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:02:00Z')
    row = _rows(db)['downloads'][0]
    assert row['requestCount'] == 2 and row['lastSeenAt'] == '2026-10-14T10:02:00Z'


def test_new_row_after_window(db):
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:00Z')
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:16:00Z')
    assert _rows(db)['total'] == 2


def test_different_client_is_a_new_row(db):
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:00Z')
    db.record_download(SLUG, EPISODE, '203.0.113.6', 'Pocket Casts', now='2026-10-14T10:01:00Z')
    assert _rows(db)['total'] == 2


def test_record_never_raises(db):
    db.record_download('missing-feed', EPISODE, IP, 'x')
    assert _rows(db)['total'] == 0


def test_joins_subscriber_label_and_filters(db):
    key = db.create_feed_subscriber_key(SLUG, 'Phone')
    db.record_download(SLUG, EPISODE, IP, 'a', subscriber_key_id=key['id'],
                       now='2026-10-14T10:00:00Z')
    db.record_download(SLUG, EPISODE, '203.0.113.6', 'b', now='2026-10-14T11:00:00Z')
    rows = _rows(db, sort_by='subscriber', sort_dir='asc')['downloads']
    assert {r['subscriberLabel'] for r in rows} == {'Phone', None}
    only = _rows(db, subscriber_key_id=key['id'])
    assert only['total'] == 1 and only['downloads'][0]['clientIp'] == IP


def test_row_carries_subscriber_key_id_and_revoked_flag(db):
    key = db.create_feed_subscriber_key(SLUG, 'Phone')
    db.record_download(SLUG, EPISODE, IP, 'a', subscriber_key_id=key['id'],
                       now='2026-10-14T10:00:00Z')
    db.record_download(SLUG, EPISODE, '203.0.113.6', 'b', now='2026-10-14T11:00:00Z')
    by_ip = {r['clientIp']: r for r in _rows(db)['downloads']}
    assert by_ip[IP]['subscriberKeyId'] == key['id']
    assert by_ip[IP]['subscriberRevoked'] is False
    assert by_ip[IP]['podcastSlug'] == SLUG
    assert by_ip['203.0.113.6']['subscriberKeyId'] is None
    assert by_ip['203.0.113.6']['subscriberRevoked'] is False
    db.revoke_feed_subscriber_key(SLUG, key['id'])
    assert _rows(db, subscriber_key_id=key['id'])['downloads'][0]['subscriberRevoked'] is True


def test_capture_request_redacts_key_and_allowlists_headers():
    line, headers = capture_request(
        'GET', '/episodes/example-podcast/a1b2c3d4e5f6.mp3', 'key=sekret&v=2',
        {'Range': 'bytes=0-', 'Cookie': 'session=x', 'Authorization': 'Bearer y',
         'Accept': ''})
    assert line == 'GET /episodes/example-podcast/a1b2c3d4e5f6.mp3?key=[redacted]&v=2'
    assert 'sekret' not in line
    assert json.loads(headers) == {'Range': 'bytes=0-'}


def test_capture_request_redacts_key_in_referer():
    _, headers = capture_request(
        'GET', '/x.mp3', '', {'Referer': 'https://example.com/p?key=sekret&x=1'})
    assert json.loads(headers) == {'Referer': 'https://example.com/p?key=[redacted]&x=1'}


def test_capture_request_caps_total_header_size():
    big = 'x' * downloads.REQUEST_HEADER_VALUE_MAX
    with patch.object(downloads, 'REQUEST_HEADERS_JSON_MAX', 700):
        _, headers = capture_request(
            'GET', '/x.mp3', '',
            {'Range': 'bytes=0-', 'Accept': big, 'Accept-Encoding': big, 'Referer': big,
             'If-Range': big, 'If-None-Match': big})
    assert len(headers) <= 700
    kept = list(json.loads(headers))
    assert kept == list(downloads.REQUEST_HEADERS)[:len(kept)] and 'If-None-Match' not in kept


def test_request_columns_added_to_existing_table(db):
    db.record_download(SLUG, EPISODE, IP, 'a', now='2026-10-14T10:00:00Z')
    conn = db.get_connection()
    conn.execute("ALTER TABLE episode_downloads DROP COLUMN last_request_line")
    conn.execute("ALTER TABLE episode_downloads DROP COLUMN last_request_headers")
    conn.commit()
    db._run_schema_migrations()
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(episode_downloads)")}
    assert {'last_request_line', 'last_request_headers'} <= cols
    row = _rows(db)['downloads'][0]
    assert row['clientIp'] == IP and row['lastRequestLine'] is None


def test_capture_request_without_query_or_headers():
    assert capture_request('GET', '/x.mp3', '', {}) == ('GET /x.mp3', None)


def test_request_is_stored_and_updated_on_bump(db):
    db.record_download(SLUG, EPISODE, IP, 'a', now='2026-10-14T10:00:00Z',
                       request_line='GET /a', request_headers='{"Range": "bytes=0-"}')
    row = _rows(db)['downloads'][0]
    assert row['lastRequestLine'] == 'GET /a'
    assert row['lastRequestHeaders'] == {'Range': 'bytes=0-'}
    db.record_download(SLUG, EPISODE, IP, 'a', now='2026-10-14T10:05:00Z',
                       request_line='GET /b', request_headers=None)
    row = _rows(db)['downloads'][0]
    assert row['lastRequestLine'] == 'GET /b' and row['lastRequestHeaders'] is None
    db.record_download(SLUG, EPISODE, IP, 'a', now='2026-10-14T10:05:30Z',
                       request_line='GET /c')
    assert _rows(db)['downloads'][0]['lastRequestLine'] == 'GET /b'


def test_request_columns_null_without_capture(db):
    db.record_download(SLUG, EPISODE, IP, 'a', now='2026-10-14T10:00:00Z')
    row = _rows(db)['downloads'][0]
    assert row['lastRequestLine'] is None and row['lastRequestHeaders'] is None


def test_filters_search_and_date_range(db):
    db.record_download(SLUG, EPISODE, IP, 'Overcast/3.0', now='2026-10-10T10:00:00Z')
    db.record_download(SLUG, EPISODE, '203.0.113.6', 'AntennaPod', now='2026-10-14T10:00:00Z')
    assert _rows(db, search='203.0.113.6')['total'] == 1
    assert _rows(db, search='overcast')['total'] == 1
    assert _rows(db, since='2026-10-12')['total'] == 1
    assert _rows(db, until='2026-10-11')['total'] == 1
    assert _rows(db, podcast_slug='other')['total'] == 0


def test_prune_respects_retention(db):
    db.record_download(SLUG, EPISODE, IP, 'old', now='2026-01-01T00:00:00Z')
    db.record_download(SLUG, EPISODE, '203.0.113.6', 'new', now='2026-10-13T00:00:00Z')
    assert db.prune_downloads(90, now='2026-10-14T00:00:00Z') == 1
    assert _rows(db)['total'] == 1


def test_prune_drops_unreferenced_geo_rows(db):
    db.record_download(SLUG, EPISODE, IP, 'old', now='2026-01-01T00:00:00Z')
    db.record_download(SLUG, EPISODE, '203.0.113.6', 'new', now='2026-10-13T00:00:00Z')
    db.store_geo_many([(IP, 'ok', 'Exampleland', None, None)])
    db.store_geo_many([('203.0.113.6', 'ok', 'Exampleland', None, None)])
    db.prune_downloads(90, now='2026-10-14T00:00:00Z')
    ips = [r['ip'] for r in db.get_connection().execute('SELECT ip FROM ip_geo_cache')]
    assert ips == ['203.0.113.6']


def test_concurrent_first_requests_share_one_row(db):
    db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:00:00Z')
    real = downloads._recent_download
    calls = []

    def stale_first(*args):
        calls.append(1)
        return None if len(calls) == 1 else real(*args)

    with patch.object(downloads, '_recent_download', stale_first):
        db.record_download(SLUG, EPISODE, IP, 'Pocket Casts', now='2026-10-14T10:02:00Z')
    result = _rows(db)
    assert result['total'] == 1 and result['downloads'][0]['requestCount'] == 2


def test_geo_pending_and_store(db):
    db.record_download(SLUG, EPISODE, IP, 'a')
    assert db.pending_geo_ips() == [IP]
    db.store_geo_many([(IP, 'ok', 'Exampleland', 'Region', 'Town')])
    assert db.pending_geo_ips() == []
    row = _rows(db)['downloads'][0]
    assert (row['geoStatus'], row['country'], row['city']) == ('ok', 'Exampleland', 'Town')


def test_failed_geo_retries_after_a_day(db):
    db.record_download(SLUG, EPISODE, IP, 'a')
    db.store_geo_many([(IP, 'failed', None, None, None)], now='2026-10-13T00:00:00Z')
    assert db.pending_geo_ips(now='2026-10-13T12:00:00Z') == []
    assert db.pending_geo_ips(now='2026-10-14T01:00:00Z') == [IP]
