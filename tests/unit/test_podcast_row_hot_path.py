"""Id-only podcast lookups use the cheap row accessor, not the episode aggregation."""
import pytest

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
