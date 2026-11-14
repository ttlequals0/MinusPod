"""Id-only podcast lookups use the cheap row accessor, not the episode aggregation."""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('podcast_row_hot_path_test_')

SLUG = 'example-podcast'
AGGREGATE_KEYS = {'episode_count', 'processed_count', 'last_episode_date',
                  'awaiting_approval_count'}


@pytest.fixture
def feed(temp_db, monkeypatch):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    temp_db.upsert_episode(SLUG, 'a1b2c3d4e5f6', title='One', status='discovered')

    def boom(*_a, **_k):
        raise AssertionError('get_podcast_by_slug ran the episode aggregation')

    monkeypatch.setattr(temp_db, 'get_podcast_by_slug', boom)
    return temp_db


def test_row_matches_aggregate_row_minus_aggregates(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    temp_db.upsert_episode(SLUG, 'a1b2c3d4e5f6', title='One')
    full = temp_db.get_podcast_by_slug(SLUG)
    row = temp_db.get_podcast_row(SLUG)
    stripped = {k: v for k, v in full.items()
                if k not in AGGREGATE_KEYS and not k.startswith('status_')}
    assert row == stripped
    assert temp_db.get_podcast_row('missing') is None
    assert temp_db.get_podcast_by_slug('missing') is None


def test_episode_lookups_skip_the_aggregation(feed):
    pid = feed.get_podcast_row(SLUG)['id']
    assert feed.has_episodes(SLUG) is True
    assert [e['episode_id'] for e in feed.get_episodes(SLUG)[0]] == ['a1b2c3d4e5f6']
    assert feed.get_episode_neighbors(SLUG, 'a1b2c3d4e5f6') == {'previous': None, 'next': None}
    statuses, _ = feed.get_episode_statuses_for_podcast(SLUG)
    assert statuses == {'a1b2c3d4e5f6': 'discovered'}
    assert feed.get_episode_statuses_for_podcast(SLUG, podcast_id=pid)[0] == statuses
    assert feed.batch_set_episodes_pending(SLUG, ['a1b2c3d4e5f6']) == 1


def test_unknown_slug_results_are_unchanged(feed):
    assert feed.has_episodes('missing') is False
    assert feed.get_episodes('missing') == ([], 0)
    assert feed.get_episode_statuses_for_podcast('missing') == ({}, {})
    assert feed.batch_set_episodes_pending('missing', ['x']) == 0
    assert feed.get_max_episodes_for_podcast('missing') == feed.DEFAULT_MAX_FEED_EPISODES


def test_feed_scoped_resolvers_and_keys_skip_the_aggregation(feed):
    feed.update_podcast(SLUG, max_episodes=7)
    assert feed.get_max_episodes_for_podcast(SLUG) == 7
    assert feed.create_feed_subscriber_key(SLUG, 'Phone')
    assert feed.upsert_episode_for_processing(SLUG, 'a1b2c3d4e5f6', 'https://example.com/a.mp3') is not None


def test_all_rows_match_the_aggregated_listing(temp_db):
    for slug in ('zeta', 'alpha', 'mid'):
        temp_db.create_podcast(slug, f'https://example.com/{slug}.xml', slug.title())
    temp_db.upsert_episode('mid', 'a1b2c3d4e5f6', title='One')
    full = temp_db.get_all_podcasts()
    rows = temp_db.get_all_podcast_rows()
    assert [r['slug'] for r in rows] == [p['slug'] for p in full]
    assert all(not (AGGREGATE_KEYS & set(r)) for r in rows)
    assert [r['id'] for r in rows] == [p['id'] for p in full]
    assert temp_db.count_podcasts() == len(full) == 3


def test_refresh_all_feeds_route_counts_without_the_aggregation(app_client, temp_db, monkeypatch):
    import main_app.feeds as feeds_module
    from tests.integration.fingerprint_helpers import csrf
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    monkeypatch.setattr(feeds_module, 'refresh_all_feeds', lambda force=False: {'success': True})

    def boom(*_a, **_k):
        raise AssertionError('get_all_podcasts ran the episode aggregation')

    monkeypatch.setattr(temp_db, 'get_all_podcasts', boom)
    response = app_client.post('/api/v1/feeds/refresh', json={}, headers=csrf(app_client))
    assert response.status_code == 200
    assert response.get_json()['feedCount'] == 1
