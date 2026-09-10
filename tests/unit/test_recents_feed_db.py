"""Recents feed membership (#721): processed episodes published on or after
the recents row's creation, from every subscribed or local feed."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recents_db_test_')

import database  # noqa: E402
from database.episodes import normalize_published_at  # noqa: E402
from database.podcasts import RECENTS_SLUG, has_upstream, is_recents_feed, recents_cutoff  # noqa: E402


def _db():
    return database.Database()


@pytest.fixture(autouse=True)
def _clean_rows():
    yield
    for slug in ('alpha', 'beta', 'gamma', 'delta', RECENTS_SLUG):
        _db().delete_podcast(slug)


def _seed_source(slug, feed_type='subscribed'):
    _db().create_podcast(slug, f'https://example.com/{slug}.xml', slug.title(), feed_type=feed_type)


def _seed_episode(slug, eid, published_at, status='processed', processed_file='/x.mp3'):
    _db().upsert_episode(slug, eid, original_url=f'https://example.com/{eid}.mp3', title=eid,
                         status=status, published_at=published_at, processed_file=processed_file)


def test_is_recents_feed_matches_only_the_recents_type():
    assert is_recents_feed({'feed_type': 'recents'}) is True
    assert is_recents_feed({'feed_type': 'local'}) is False
    assert is_recents_feed(None) is False


def test_cutoff_is_the_creation_day():
    assert recents_cutoff({'created_at': '2026-09-06T17:30:00Z'}) == '2026-09-06'


def test_membership_uses_publish_date_not_processing_time():
    db = _db()
    _seed_source('alpha')
    _seed_source('beta', feed_type='local')
    _seed_episode('alpha', 'aaaaaaaaaaa1', '2026-09-01T00:00:00Z')
    _seed_episode('alpha', 'aaaaaaaaaaa2', '2026-09-10T00:00:00Z')
    _seed_episode('beta', 's01e01', '2026-09-11T00:00:00Z')
    _seed_episode('beta', 's01e02', '2026-09-12T00:00:00Z', status='pending', processed_file=None)
    _seed_episode('alpha', 'aaaaaaaaaaa3', None)
    rows = db.get_recent_processed_episodes('2026-09-05')
    assert [(r['source_slug'], r['episode_id']) for r in rows] == [
        ('beta', 's01e01'), ('alpha', 'aaaaaaaaaaa2')]
    assert db.count_recent_processed_episodes('2026-09-05') == 2
    assert rows[0]['source_title'] == 'Beta' and rows[0]['source_feed_type'] == 'local'


def test_membership_paginates_and_never_includes_a_recents_row():
    db = _db()
    _seed_source('gamma')
    db.create_podcast(RECENTS_SLUG, 'recents://', 'Recents', feed_type='recents')
    for i in range(3):
        _seed_episode('gamma', f'ccccccccccc{i}', f'2026-09-1{i}T00:00:00Z')
    rows = db.get_recent_processed_episodes('2026-09-01', limit=2, offset=1)
    assert [r['episode_id'] for r in rows] == ['ccccccccccc1', 'ccccccccccc0']
    assert db.count_recent_processed_episodes('2026-09-01') == 3


def test_membership_carries_chapters_and_transcript_flags():
    db = _db()
    _seed_source('delta')
    _seed_episode('delta', 'ddddddddddd1', '2026-09-10T00:00:00Z')
    db.save_episode_details('delta', 'ddddddddddd1', chapters_json=json.dumps({'chapters': []}),
                            transcript_vtt='WEBVTT')
    rows = db.get_recent_processed_episodes('2026-09-01', details=True)
    assert rows[0]['chapters_json'] == json.dumps({'chapters': []})
    assert rows[0]['has_transcript_vtt'] == 1


def test_has_upstream_is_false_for_local_and_recents():
    assert has_upstream({'feed_type': 'subscribed'}) is True
    assert has_upstream({'slug': 'x'}) is True
    assert has_upstream({'feed_type': 'local'}) is False
    assert has_upstream({'feed_type': 'recents'}) is False
    assert has_upstream(None) is False


def test_published_at_is_stored_as_true_utc():
    # A publisher offset must not survive: the cutoff and cross-feed order compare strings.
    assert normalize_published_at('Sat, 05 Sep 2026 20:00:00 -0700') == '2026-09-06T03:00:00Z'
    assert normalize_published_at('2026-09-06T01:00:00+02:00') == '2026-09-05T23:00:00Z'
    assert normalize_published_at('2026-09-06T01:00:00Z') == '2026-09-06T01:00:00Z'
    assert normalize_published_at('not a date') == 'not a date'


def test_source_title_prefers_override_then_title_then_slug():
    _seed_source('alpha')
    _db().update_podcast('alpha', title_override='Renamed Alpha')
    _db().create_podcast('gamma', 'https://example.com/gamma.xml', None, feed_type='subscribed')
    _seed_episode('alpha', 'a1', '2026-09-06T10:00:00Z')
    _seed_episode('gamma', 'g1', '2026-09-06T11:00:00Z')
    rows = {r['episode_id']: r['source_title'] for r in _db().get_recent_processed_episodes('2026-09-06')}
    assert rows == {'a1': 'Renamed Alpha', 'g1': 'gamma'}


def test_detail_flags_only_with_details_and_sort_is_whitelisted():
    _seed_source('alpha')
    _seed_episode('alpha', 'a1', '2026-09-06T10:00:00Z')
    _seed_episode('alpha', 'a2', '2026-09-07T10:00:00Z')
    plain = _db().get_recent_processed_episodes('2026-09-06')
    assert 'has_transcript_vtt' not in plain[0] and 'source_chapters_in_notes' in plain[0]
    detailed = _db().get_recent_processed_episodes('2026-09-06', details=True)
    assert detailed[0]['has_transcript_vtt'] == 0 and 'chapters_json' in detailed[0]
    oldest_first = _db().get_recent_processed_episodes('2026-09-06', sort_dir='asc')
    assert [r['episode_id'] for r in oldest_first] == ['a1', 'a2']
    bogus = _db().get_recent_processed_episodes('2026-09-06', sort_by='1; DROP TABLE episodes')
    assert [r['episode_id'] for r in bogus] == ['a2', 'a1']
