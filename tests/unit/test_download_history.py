"""Download history: dedupe, listing, pruning and geo cache bookkeeping."""
from unittest.mock import patch

import pytest

from database import downloads

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
    db.store_geo(IP, 'ok', 'Exampleland')
    db.store_geo('203.0.113.6', 'ok', 'Exampleland')
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
    db.store_geo(IP, 'ok', country='Exampleland', region='Region', city='Town')
    assert db.pending_geo_ips() == []
    row = _rows(db)['downloads'][0]
    assert (row['geoStatus'], row['country'], row['city']) == ('ok', 'Exampleland', 'Town')


def test_failed_geo_retries_after_a_day(db):
    db.record_download(SLUG, EPISODE, IP, 'a')
    db.store_geo(IP, 'failed', now='2026-10-13T00:00:00Z')
    assert db.pending_geo_ips(now='2026-10-13T12:00:00Z') == []
    assert db.pending_geo_ips(now='2026-10-14T01:00:00Z') == [IP]
