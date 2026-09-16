"""Integration tests for feed-list pagination and the opt-in latest-episode
projection: GET /feeds?page=&limit= and
GET /feeds?includeLatestEpisodes=true&episodesPerFeed=N.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feeds-projection-test-'))


def _make_feed(db, slug, title):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', title=title)
    return slug


def _seed_episode(db, slug, episode_id, *, published_at=None, status='processed'):
    db.upsert_episode(
        slug, episode_id, original_url=f'https://example.com/{slug}/{episode_id}.mp3',
        title=f'{slug}-{episode_id}', status=status,
        published_at=published_at, original_duration=120.0,
    )


@pytest.fixture
def feeds(app_client):
    from api import get_database
    db = get_database()
    slugs = [_make_feed(db, f'projection-feed-{i}', f'Projection Feed {i}') for i in range(5)]
    yield {'slugs': slugs, 'db': db}
    for slug in slugs:
        db.delete_podcast(slug)


def test_default_feeds_stays_backward_compatible(app_client, feeds):
    body = app_client.get('/api/v1/feeds').get_json()
    assert 'feeds' in body
    slugs = {f['slug'] for f in body['feeds']}
    assert set(feeds['slugs']).issubset(slugs)
    # Adds pagination metadata without truncating the unbounded default.
    assert body['total'] >= len(feeds['slugs'])
    assert body['totalPages'] == 1
    assert body['page'] == 1
    assert 'lastRefreshCompletedAt' in body


def test_page_and_limit_paginate_feeds(app_client, feeds):
    first = app_client.get('/api/v1/feeds?page=1&limit=2').get_json()
    assert len(first['feeds']) == 2
    assert first['page'] == 1
    assert first['limit'] == 2
    assert first['total'] >= 5
    assert first['totalPages'] >= 3

    second = app_client.get('/api/v1/feeds?page=2&limit=2').get_json()
    assert len(second['feeds']) == 2
    first_slugs = {f['slug'] for f in first['feeds']}
    second_slugs = {f['slug'] for f in second['feeds']}
    assert first_slugs.isdisjoint(second_slugs)


def test_latest_episodes_ordered_bounded_and_includes_unprocessed(app_client, feeds):
    db = feeds['db']
    slug = feeds['slugs'][0]
    _seed_episode(db, slug, 'ep-1', published_at='2026-01-01T00:00:00Z')
    _seed_episode(db, slug, 'ep-2', published_at='2026-01-03T00:00:00Z')
    _seed_episode(db, slug, 'ep-3', published_at='2026-01-02T00:00:00Z')
    _seed_episode(db, slug, 'ep-4', published_at=None, status='discovered')

    body = app_client.get(
        '/api/v1/feeds?includeLatestEpisodes=true&episodesPerFeed=3').get_json()
    match = next(f for f in body['feeds'] if f['slug'] == slug)
    latest = match['latestEpisodes']
    assert len(latest) == 3
    ids = [ep['id'] for ep in latest]
    # ep-2 (Jan 3) newest, then ep-3 (Jan 2), then ep-1 (Jan 1); ep-4 (no
    # published_at, falls back to created_at ~now) sorts ahead of all three
    # but the bound keeps only 3, so ep-4 must be present and ep-1 dropped.
    assert 'ep-4' in ids
    assert 'ep-1' not in ids
    statuses = {ep['id']: ep['status'] for ep in latest}
    assert statuses['ep-4'] == 'discovered'


def test_feed_with_fewer_episodes_returns_fewer(app_client, feeds):
    db = feeds['db']
    slug = feeds['slugs'][1]
    _seed_episode(db, slug, 'only-ep', published_at='2026-01-01T00:00:00Z')

    body = app_client.get(
        '/api/v1/feeds?includeLatestEpisodes=true&episodesPerFeed=3').get_json()
    match = next(f for f in body['feeds'] if f['slug'] == slug)
    assert len(match['latestEpisodes']) == 1
    assert match['latestEpisodes'][0]['id'] == 'only-ep'


def test_feed_with_no_episodes_returns_empty_projection(app_client, feeds):
    slug = feeds['slugs'][2]
    body = app_client.get(
        '/api/v1/feeds?includeLatestEpisodes=true&episodesPerFeed=3').get_json()
    match = next(f for f in body['feeds'] if f['slug'] == slug)
    assert match['latestEpisodes'] == []


def test_episodes_per_feed_is_clamped_not_unbounded(app_client, feeds, monkeypatch):
    from api import get_database
    db = get_database()
    slug = feeds['slugs'][3]
    for i in range(25):
        _seed_episode(db, slug, f'ep-{i}', published_at=f'2026-01-{i + 1:02d}T00:00:00Z')

    body = app_client.get(
        '/api/v1/feeds?includeLatestEpisodes=true&episodesPerFeed=99999').get_json()
    match = next(f for f in body['feeds'] if f['slug'] == slug)
    # Clamped well below the 25 seeded episodes.
    assert 0 < len(match['latestEpisodes']) < 25


def test_latest_episodes_projection_uses_one_query_not_per_feed(app_client, feeds):
    """The batched projection must not fan out one query per feed on the page."""
    from api import get_database
    db = get_database()
    for slug in feeds['slugs']:
        _seed_episode(db, slug, 'ep-a', published_at='2026-01-01T00:00:00Z')

    calls = []
    original = db.get_latest_episodes_for_podcasts

    def _counting(podcast_ids, per_feed_limit):
        calls.append(podcast_ids)
        return original(podcast_ids, per_feed_limit)

    db.get_latest_episodes_for_podcasts = _counting
    try:
        body = app_client.get(
            '/api/v1/feeds?includeLatestEpisodes=true&episodesPerFeed=3').get_json()
    finally:
        del db.get_latest_episodes_for_podcasts

    assert len(calls) == 1
    assert len(calls[0]) >= len(feeds['slugs'])
    assert all(f.get('latestEpisodes') for f in body['feeds']
              if f['slug'] in feeds['slugs'])


def test_latest_episode_projection_carries_hold_and_passthrough_signals(app_client, feeds):
    """A held or pass-through episode must not lose that on the dashboard."""
    db = feeds['db']
    slug = feeds['slugs'][4]
    _seed_episode(db, slug, 'ep-held', published_at='2026-02-01T00:00:00Z')
    podcast = db.get_podcast_by_slug(slug)
    db.upsert_episode(slug, 'ep-held', error_message='boom',
                      processed_at='2026-02-01T01:00:00Z')
    db.save_episode_details(slug, 'ep-held', pending_review_count=2)
    db.set_episodes_passthrough(slug, ['ep-held'], True)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Projection',
        episode_id='ep-held', episode_title='Held', status='completed',
        ads_detected=1)

    body = app_client.get(
        '/api/v1/feeds?includeLatestEpisodes=true&episodesPerFeed=3').get_json()
    match = next(f for f in body['feeds'] if f['slug'] == slug)
    episode = next(ep for ep in match['latestEpisodes'] if ep['id'] == 'ep-held')
    assert episode['pendingReviewCount'] == 2
    assert episode['passthroughEnabled'] is True
    assert episode['error'] == 'boom'
    assert episode['processedAt'] == '2026-02-01T01:00:00Z'
    assert episode['hasBeenProcessed'] is True
