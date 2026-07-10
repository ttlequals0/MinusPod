"""Integration tests for per-feed episode status counts (#466).

GET /feeds and GET /feeds/{slug} return a statusCounts object whose keys match
the frontend status-badge keys: the DB status 'processed' is exposed under its
API alias 'completed'.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feed-status-test-'))

ALL_KEYS = {'discovered', 'pending', 'processing', 'completed',
            'failed', 'permanently_failed', 'deferred'}


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'status-counts-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Status Counts Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def _seed_episodes(db, slug):
    statuses = ['discovered', 'discovered', 'pending', 'processing',
                'processed', 'processed', 'processed', 'failed',
                'permanently_failed']
    for i, status in enumerate(statuses):
        db.upsert_episode(slug, f'ep-{i}', title=f'Episode {i}', status=status)


def test_get_feed_status_counts_with_completed_alias(app_client, seeded_feed):
    slug, db = seeded_feed['slug'], seeded_feed['db']
    _seed_episodes(db, slug)
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    counts = body['statusCounts']
    assert set(counts.keys()) == ALL_KEYS
    assert counts['discovered'] == 2
    assert counts['pending'] == 1
    assert counts['processing'] == 1
    assert counts['completed'] == 3
    assert counts['failed'] == 1
    assert counts['permanently_failed'] == 1
    assert counts['deferred'] == 0


def test_list_feeds_includes_status_counts(app_client, seeded_feed):
    slug, db = seeded_feed['slug'], seeded_feed['db']
    _seed_episodes(db, slug)
    feeds = app_client.get('/api/v1/feeds').get_json()['feeds']
    match = next((f for f in feeds if f['slug'] == slug), None)
    assert match is not None
    assert match['statusCounts']['completed'] == 3
    assert match['statusCounts']['discovered'] == 2


def test_empty_feed_returns_all_zeros(app_client, seeded_feed):
    """LEFT JOIN SUMs are NULL for a feed with no episodes; the API must
    coerce them to 0."""
    slug = seeded_feed['slug']
    counts = app_client.get(f'/api/v1/feeds/{slug}').get_json()['statusCounts']
    assert set(counts.keys()) == ALL_KEYS
    assert all(v == 0 for v in counts.values())
