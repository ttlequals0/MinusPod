"""Recents feed membership (#721): processed episodes published on or after
the recents row's creation, from every subscribed or local feed."""
import json

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recents_db_test_')

import database  # noqa: E402
from database.podcasts import RECENTS_SLUG, is_recents_feed  # noqa: E402


def _db():
    return database.Database()


def _seed_source(slug, feed_type='subscribed'):
    _db().create_podcast(slug, f'https://example.com/{slug}.xml', slug.title(), feed_type=feed_type)


def _seed_episode(slug, eid, published_at, status='processed', processed_file='/x.mp3'):
    _db().upsert_episode(slug, eid, original_url=f'https://example.com/{eid}.mp3', title=eid,
                         status=status, published_at=published_at, processed_file=processed_file)


def test_is_recents_feed_matches_only_the_recents_type():
    assert is_recents_feed({'feed_type': 'recents'}) is True
    assert is_recents_feed({'feed_type': 'local'}) is False
    assert is_recents_feed(None) is False


def test_get_recents_feed_returns_the_single_row():
    db = _db()
    assert db.get_recents_feed() is None
    db.create_podcast(RECENTS_SLUG, 'recents://', 'Recents', feed_type='recents')
    row = db.get_recents_feed()
    assert row['slug'] == RECENTS_SLUG and row['feed_type'] == 'recents'
    db.delete_podcast(RECENTS_SLUG)


def test_membership_uses_publish_date_not_processing_time():
    db = _db()
    _seed_source('alpha')
    _seed_source('beta', feed_type='local')
    _seed_episode('alpha', 'aaaaaaaaaaa1', '2026-09-01T00:00:00Z')
    _seed_episode('alpha', 'aaaaaaaaaaa2', '2026-09-10T00:00:00Z')
    _seed_episode('beta', 's01e01', '2026-09-11T00:00:00Z')
    _seed_episode('beta', 's01e02', '2026-09-12T00:00:00Z', status='pending', processed_file=None)
    _seed_episode('alpha', 'aaaaaaaaaaa3', None)
    rows, total = db.get_recent_processed_episodes('2026-09-05T00:00:00Z')
    assert [(r['source_slug'], r['episode_id']) for r in rows] == [
        ('beta', 's01e01'), ('alpha', 'aaaaaaaaaaa2')]
    assert total == 2
    assert rows[0]['source_title'] == 'Beta'
    db.delete_podcast('alpha')
    db.delete_podcast('beta')


def test_membership_paginates_and_never_includes_a_recents_row():
    db = _db()
    _seed_source('gamma')
    db.create_podcast(RECENTS_SLUG, 'recents://', 'Recents', feed_type='recents')
    for i in range(3):
        _seed_episode('gamma', f'ccccccccccc{i}', f'2026-09-1{i}T00:00:00Z')
    rows, total = db.get_recent_processed_episodes('2026-09-01T00:00:00Z', limit=2, offset=1)
    assert total == 3 and [r['episode_id'] for r in rows] == ['ccccccccccc1', 'ccccccccccc0']
    db.delete_podcast('gamma')
    db.delete_podcast(RECENTS_SLUG)


def test_membership_carries_chapters_json():
    db = _db()
    _seed_source('delta')
    _seed_episode('delta', 'ddddddddddd1', '2026-09-10T00:00:00Z')
    db.save_episode_details('delta', 'ddddddddddd1', chapters_json=json.dumps({'chapters': []}))
    rows, _ = db.get_recent_processed_episodes('2026-09-01T00:00:00Z')
    assert rows[0]['chapters_json'] == json.dumps({'chapters': []})
    db.delete_podcast('delta')
